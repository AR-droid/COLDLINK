"""Every Cypher query Coldlink depends on, in one place.

Scores are computed here, in Cypher, from independent evidence types, so the
numbers are reproducible. The language model only chooses which tools to call
and writes the explanation.
"""

# ---------------------------------------------------------------- schema
INDEXES = [
    "CREATE INDEX FOR (n:Case) ON (n.id)",
    "CREATE INDEX FOR (n:Case) ON (n.type)",
    "CREATE INDEX FOR (n:Exhibit) ON (n.id)",
    "CREATE INDEX FOR (n:Exhibit) ON (n.case)",
    "CREATE INDEX FOR (n:ToolMark) ON (n.tm_class)",
    "CREATE INDEX FOR (n:Footwear) ON (n.pattern)",
    "CREATE INDEX FOR (n:Vehicle) ON (n.make_model)",
    "CREATE INDEX FOR (n:Finding) ON (n.id)",
    "CREATE INDEX FOR (n:Decision) ON (n.id)",
    "CREATE INDEX FOR (n:CustodyEvent) ON (n.id)",
    "CREATE INDEX FOR (n:Examiner) ON (n.id)",
    "CREATE INDEX FOR (n:Method) ON (n.id)",
    "CREATE INDEX FOR (n:SOP) ON (n.id)",
    "CREATE INDEX FOR (n:Camera) ON (n.id)",
    "CREATE INDEX FOR (n:Station) ON (n.id)",
    "CREATE INDEX FOR (n:Location) ON (n.id)",
    "CREATE INDEX FOR (n:Statement) ON (n.id)",
    "CREATE INDEX FOR (n:Hypothesis) ON (n.id)",
    "CREATE INDEX FOR (n:ActionRecord) ON (n.id)",
    "CREATE INDEX FOR (n:AgentRun) ON (n.id)",
]

# Link-score weights. Physical evidence types are independent of each other
# and of the modus operandi, so their contributions add.
W_TOOLMARK, W_FOOTWEAR, W_VEHICLE = 0.40, 0.35, 0.25
W_ENTRY, W_TIME = 0.10, 0.05
C_ENTRY, C_TIME = 0.10, 0.10
LINK_THRESHOLD = 0.35

# ------------------------------------------------- exhibit similarity edges
# $scope: null for the whole graph, or a case id to only compare that case's
# exhibits against the full history (used when a new case arrives).
SIMILAR_TOOLMARK = """
MATCH (a:ToolMark) WHERE $scope IS NULL OR a.case = $scope
MATCH (b:ToolMark {tm_class: a.tm_class})
WHERE a.case <> b.case AND ($scope IS NOT NULL OR a.id < b.id)
  AND abs(a.width_mm - b.width_mm) <= 0.4
WITH a, b, 1.0 - abs(a.width_mm - b.width_mm) / 0.8 AS w, a.edge_profile = b.edge_profile AS same
CREATE (a)-[:SIMILAR_TO {etype: 'toolmark',
  score: CASE WHEN same THEN w ELSE w * 0.25 END,
  contra: CASE WHEN same THEN 0.0 ELSE 0.30 END,
  basis: a.tm_class + ' width ' + a.width_label + ' vs ' + b.width_label + ' mm; edge ' +
         CASE WHEN same THEN 'profile matches (' + a.edge_profile + ')'
              ELSE 'profile differs (' + a.edge_profile + ' vs ' + b.edge_profile + ')' END}]->(b)
RETURN count(*) AS created
"""

# Sub-type is only compared when both exhibits recorded it (SOP-FW-3).
SIMILAR_FOOTWEAR = """
MATCH (a:Footwear) WHERE $scope IS NULL OR a.case = $scope
MATCH (b:Footwear {pattern: a.pattern})
WHERE a.case <> b.case AND ($scope IS NOT NULL OR a.id < b.id) AND a.size = b.size
  AND NOT (a.subtype IS NOT NULL AND b.subtype IS NOT NULL AND a.subtype <> b.subtype)
WITH a, b, (a.subtype IS NOT NULL AND b.subtype IS NOT NULL) AS sub_known, a.wear_code = b.wear_code AS wear
CREATE (a)-[:SIMILAR_TO {etype: 'footwear',
  score: 0.6 + CASE WHEN sub_known THEN 0.25 ELSE 0.0 END + CASE WHEN wear THEN 0.15 ELSE 0.0 END,
  contra: 0.0,
  basis: a.pattern + ' size ' + toString(a.size) +
         CASE WHEN sub_known THEN ', sub-type ' + a.subtype + ' matches' ELSE ', sub-type not compared (not recorded)' END +
         CASE WHEN wear THEN ', wear ' + a.wear_code + ' matches' ELSE '' END}]->(b)
RETURN count(*) AS created
"""

SIMILAR_VEHICLE = """
MATCH (a:Vehicle) WHERE $scope IS NULL OR a.case = $scope
MATCH (b:Vehicle {make_model: a.make_model})
WHERE a.case <> b.case AND ($scope IS NOT NULL OR a.id < b.id) AND a.colour = b.colour
  AND left(a.plate_partial, 1) = left(b.plate_partial, 1)
WITH a, b, a.plate_partial = b.plate_partial AS plate
CREATE (a)-[:SIMILAR_TO {etype: 'vehicle',
  score: CASE WHEN plate THEN 1.0 ELSE 0.4 END, contra: 0.0,
  basis: a.colour + ' ' + a.make_model + CASE WHEN plate THEN ', partial plate ' + a.plate_partial + ' matches' ELSE ', plate first letter only' END}]->(b)
RETURN count(*) AS created
"""

# --------------------------------------------------------- case link score
# Shared scoring body. Expects c1, c2 bound. Supporting and contradicting
# evidence are scored separately; score = support - contra.
_SCORE_BODY = f"""
OPTIONAL MATCH (c1)-[:HAS_EXHIBIT]->(e1)-[s:SIMILAR_TO]-(e2)<-[:HAS_EXHIBIT]-(c2)
WITH c1, c2, s.etype AS t, max(s.score) AS sc, max(s.contra) AS ct, collect(s.basis)[0] AS basis,
     collect(e1.id + '~' + e2.id)[0] AS pair
WITH c1, c2, [x IN collect({{t: t, sc: sc, ct: ct, basis: basis, pair: pair}}) WHERE x.t IS NOT NULL] AS ev
WITH c1, c2, ev,
  reduce(a = 0.0, x IN ev | a + x.sc * CASE x.t WHEN 'toolmark' THEN {W_TOOLMARK} WHEN 'footwear' THEN {W_FOOTWEAR} ELSE {W_VEHICLE} END) AS phys,
  reduce(a = 0.0, x IN ev | a + x.ct) AS phys_contra,
  CASE WHEN abs(c1.hour - c2.hour) > 12 THEN 24 - abs(c1.hour - c2.hour) ELSE abs(c1.hour - c2.hour) END AS hd,
  (c1.entry_point IS NOT NULL AND c2.entry_point IS NOT NULL) AS both_entry
WITH c1, c2, ev, phys, phys_contra, hd, both_entry,
  CASE WHEN both_entry AND c1.entry_point = c2.entry_point THEN {W_ENTRY} ELSE 0.0 END +
  CASE WHEN hd <= 2 THEN {W_TIME} ELSE 0.0 END AS mo_support,
  CASE WHEN both_entry AND c1.entry_point <> c2.entry_point THEN {C_ENTRY} ELSE 0.0 END +
  CASE WHEN hd >= 6 THEN {C_TIME} ELSE 0.0 END AS mo_contra
WITH c1, c2, ev, phys + mo_support AS support, phys_contra + mo_contra AS contra, hd, both_entry
"""

SCORE_PAIR = """
MATCH (c1:Case {id: $a}), (c2:Case {id: $b})
""" + _SCORE_BODY + """
RETURN c1.id AS a, c2.id AS b, round(1000 * support) / 1000 AS support, round(1000 * contra) / 1000 AS contra,
       round(1000 * (support - contra)) / 1000 AS score, ev AS evidence,
       c1.entry_point AS entry_a, c2.entry_point AS entry_b, c1.hour AS hour_a, c2.hour AS hour_b
"""

# Materialise LINKED edges for every case pair whose score passes the threshold.
DERIVE_LINKS = """
MATCH (c1:Case)-[:HAS_EXHIBIT]->(:Exhibit)-[:SIMILAR_TO]-(:Exhibit)<-[:HAS_EXHIBIT]-(c2:Case)
WHERE ($scope IS NULL AND c1.id < c2.id) OR ($scope IS NOT NULL AND c1.id = $scope)
WITH DISTINCT c1, c2
WHERE NOT (c1)-[:LINKED]-(c2)
""" + _SCORE_BODY + f"""
WHERE support - contra >= {LINK_THRESHOLD}
CREATE (c1)-[l:LINKED {{score: round(1000 * (support - contra)) / 1000, support: round(1000 * support) / 1000,
  contra: round(1000 * contra) / 1000, cost: 1.0 - (support - contra),
  types: [x IN ev | x.t], pairs: [x IN ev | x.pair], basis: [x IN ev | x.t + ': ' + x.basis],
  status: 'proposed', origin: 'derived'}}]->(c2)
CREATE (c1)-[:SERIES_EDGE {{cost: 1.0 - (support - contra)}}]->(c2)
RETURN count(l) AS created
"""

# ---------------------------------------------------------------- tools
FIND_SIMILAR_EXHIBITS = """
MATCH (c:Case {id: $case_id})-[:HAS_EXHIBIT]->(e:Exhibit)-[s:SIMILAR_TO]-(o:Exhibit)<-[:HAS_EXHIBIT]-(oc:Case)
WHERE ($etype IS NULL OR s.etype = $etype) AND s.score >= $min_score
  AND (oc.sealed IS NULL OR oc.sealed = false)
RETURN e.id AS exhibit, s.etype AS type, o.id AS similar_exhibit, oc.id AS other_case, oc.date AS other_date,
       oc.station AS other_station, round(1000 * s.score) / 1000 AS score, s.contra AS contra, s.basis AS basis
ORDER BY score DESC LIMIT $limit
"""

CASE_LINKS = """
MATCH (c:Case {id: $case_id})-[l:LINKED]-(o:Case)
WHERE $include_inactive OR l.status IN ['proposed', 'accepted']
RETURN o.id AS other_case, o.date AS date, o.station AS station, o.entry_point AS entry_point, o.hour AS hour,
       l.score AS score, l.support AS support, l.contra AS contra, l.types AS types, l.basis AS basis,
       l.status AS status, l.reason AS reason
ORDER BY score DESC
"""

# Strongest evidence chain between two cases: minimise the sum of (1 - score)
# over active links, in either direction.
LINK_PATH = """
MATCH (a:Case {id: $a}), (b:Case {id: $b})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['SERIES_EDGE'], weightProp: 'cost',
                   relDirection: 'both', maxLen: $max_len})
YIELD path, pathWeight
RETURN [n IN nodes(path) | n.id] AS cases, pathWeight AS cost
"""

LINK_DETAIL = """
MATCH (x:Case {id: $a})-[l:LINKED]-(y:Case {id: $b})
RETURN l.score AS score, l.types AS types, l.basis AS basis, l.pairs AS pairs, l.status AS status
"""

# Connected components over active links only (SERIES_EDGE is the projection
# of LINKED edges whose status is proposed or accepted).
SERIES_COMPONENTS = """
CALL algo.WCC({nodeLabels: ['Case'], relationshipTypes: ['SERIES_EDGE']}) YIELD node, componentId
WITH componentId, collect(node.id) AS cases
WHERE size(cases) >= $min_size
RETURN componentId, size(cases) AS n, cases ORDER BY n DESC LIMIT $limit
"""

SERIES_EDGES_AMONG = """
MATCH (a:Case)-[l:LINKED]->(b:Case)
WHERE a.id IN $cases AND b.id IN $cases AND l.status IN ['proposed', 'accepted']
RETURN a.id AS a, b.id AS b, l.score AS score, l.types AS types, l.basis AS basis, l.pairs AS pairs, l.status AS status
"""

# Reliability facts for each exhibit behind a link: SOP currency, custody, examiner.
EXHIBIT_RELIABILITY = """
UNWIND $exhibits AS xid
MATCH (e:Exhibit {id: xid})
OPTIONAL MATCH (f:Finding)-[:ABOUT]->(e)
OPTIONAL MATCH (f)-[:USED_SOP]->(sop:SOP)
OPTIONAL MATCH (newer:SOP)-[:SUPERSEDES]->(sop)
OPTIONAL MATCH (f)-[:EXAMINED_BY]->(x:Examiner)
RETURN e.id AS exhibit, e.case AS case_id, e.type AS type, sop.id AS sop, newer.id AS superseded_by,
       x.id AS examiner, x.left IS NOT NULL AS examiner_left, coalesce(e.custody_gap_days, 0) AS custody_gap_days,
       coalesce(e.retest_status, 'none') AS retest_status
"""

# Reverse traversal: every decision that rests on a source, directly or via
# findings about exhibits recorded by it (e.g. a camera).
DECISIONS_RESTING_ON = """
MATCH (src {id: $source_id})
MATCH p = (d:Decision)-[:BASED_ON]->(f:Finding)-[:ABOUT|FROM_CAMERA*1..2]->(src)
OPTIONAL MATCH (d)-[:FOR_CASE]->(c:Case)
RETURN DISTINCT d.id AS decision, d.kind AS kind, d.status AS status, d.date AS date, c.id AS case_id,
       d.text AS text, d.margin_minutes AS margin_minutes, d.time_claim AS time_claim, f.id AS via_finding,
       [n IN nodes(p) | n.id] AS path
ORDER BY date
"""

SOURCE_RELIABILITY_FINDINGS = """
MATCH (f:Finding)-[:ABOUT]->(src {id: $source_id})
WHERE f.clock_offset_minutes IS NOT NULL
RETURN f.id AS finding, f.date AS date, f.clock_offset_minutes AS clock_offset_minutes, f.text AS text
"""

# Cameras whose recordings feed decisions in a set of cases.
CAMERAS_FOR_CASES = """
MATCH (c:Case)-[:HAS_EXHIBIT]->(e:CCTV)-[:FROM_CAMERA]->(cam:Camera)
WHERE c.id IN $cases
OPTIONAL MATCH (rf:Finding)-[:ABOUT]->(cam) WHERE rf.clock_offset_minutes IS NOT NULL
RETURN DISTINCT cam.id AS camera, c.id AS case_id, e.id AS exhibit, rf.id AS reliability_finding,
       rf.clock_offset_minutes AS clock_offset_minutes
"""

# Walk the custody chain along NEXT and flag dispatch -> receipt gaps.
CUSTODY_GAPS = """
MATCH (e:Exhibit {id: $exhibit_id})-[:FIRST_CUSTODY]->(first:CustodyEvent)
MATCH (first)-[:NEXT*0..]->(a:CustodyEvent)-[:NEXT]->(b:CustodyEvent)
WITH e, a, b, (b.ts_epoch - a.ts_epoch) / 86400.0 AS days
WHERE (a.action = 'dispatched' AND days > $max_transit_days) OR days > 365
RETURN e.id AS exhibit, a.seq AS from_seq, a.action AS from_action, a.ts AS from_ts,
       b.seq AS to_seq, b.action AS to_action, b.ts AS to_ts, round(10 * days) / 10 AS gap_days
"""

CUSTODY_GAPS_ALL = """
MATCH (a:CustodyEvent)-[:NEXT]->(b:CustodyEvent)
WHERE a.action = 'dispatched' AND (b.ts_epoch - a.ts_epoch) / 86400.0 > $max_transit_days
MATCH (e:Exhibit {id: a.exhibit})
RETURN e.id AS exhibit, e.case AS case_id, a.ts AS from_ts, b.ts AS to_ts,
       round(10 * (b.ts_epoch - a.ts_epoch) / 86400.0) / 10 AS gap_days
"""

BEST_EXAMINER = """
MATCH (m:Method {id: $method})
MATCH (x:Examiner)<-[:EXAMINED_BY]-(f:Finding)-[:USED_METHOD]->(m)
OPTIONAL MATCH (f)-[:USED_SOP]->(s:SOP)
WITH x, count(f) AS total,
     sum(CASE WHEN s.valid_to IS NULL THEN 1 ELSE 0 END) AS under_current_sop,
     sum(CASE WHEN f.date >= $recent_from THEN 1 ELSE 0 END) AS recent,
     max(f.date) AS last_case
WHERE x.left IS NULL AND NOT x.id IN $exclude
RETURN x.id AS examiner, x.name AS name, total, under_current_sop, recent, last_case,
       total + 2 * under_current_sop + 3 * recent AS weight
ORDER BY weight DESC LIMIT $limit
"""

# Findings made under a procedure that has since been superseded.
FINDINGS_UNDER_SUPERSEDED = """
MATCH (newer:SOP)-[:SUPERSEDES]->(old:SOP)<-[:USED_SOP]-(f:Finding)-[:ABOUT]->(e:Exhibit)
WHERE $cases IS NULL OR e.case IN $cases
RETURN f.id AS finding, e.id AS exhibit, e.case AS case_id, old.id AS old_sop, newer.id AS current_sop, f.date AS date
"""

CASE_SUMMARY = """
MATCH (c:Case {id: $case_id})
OPTIONAL MATCH (c)-[:HAS_EXHIBIT]->(e:Exhibit)
OPTIONAL MATCH (f:Finding)-[:ABOUT]->(e)
WITH c, collect(DISTINCT {exhibit: e.id, type: e.type, finding: f.id, text: f.text, sop: f.sop}) AS exhibits
OPTIONAL MATCH (d:Decision)-[:FOR_CASE]->(c)
WITH c, exhibits, collect(DISTINCT {decision: d.id, kind: d.kind, status: d.status, text: d.text}) AS decisions
OPTIONAL MATCH (c)-[:HAS_STATEMENT]->(st:Statement)
RETURN c.id AS case_id, c.date AS date, c.station AS station, c.type AS type, c.entry_point AS entry_point,
       c.hour AS hour, c.status AS status, exhibits, [d IN decisions WHERE d.decision IS NOT NULL] AS decisions,
       collect(st.text) AS statements
"""

# ------------------------------------------------- reasoning and memory writes
SET_LINK_STATUS = """
MATCH (a:Case {id: $a})-[l:LINKED]-(b:Case {id: $b})
SET l.status = $status, l.reason = $reason, l.status_changed = $ts, l.status_by = $by
WITH a, b, l
OPTIONAL MATCH (a)-[se:SERIES_EDGE]-(b)
WITH a, b, l, collect(se) AS ses
FOREACH (x IN CASE WHEN $status IN ['proposed', 'accepted'] THEN [] ELSE ses END | DELETE x)
FOREACH (_ IN CASE WHEN $status IN ['proposed', 'accepted'] AND size(ses) = 0 THEN [1] ELSE [] END |
  CREATE (a)-[:SERIES_EDGE {cost: l.cost}]->(b))
RETURN a.id AS a, b.id AS b, $status AS status
"""

CREATE_HYPOTHESIS = """
CREATE (h:Hypothesis {id: $id, kind: 'series', status: 'open', created: $ts, created_by: $by,
                      support: $support, contra: $contra, summary: $summary})
WITH h
UNWIND $cases AS cid
MATCH (c:Case {id: cid})
CREATE (c)-[:PART_OF {status: 'proposed'}]->(h)
RETURN h.id AS hypothesis, count(c) AS cases
"""

LINK_HYPOTHESIS_FINDINGS = """
MATCH (h:Hypothesis {id: $id})
UNWIND $support AS fid
MATCH (f:Finding {id: fid})
MERGE (h)-[:SUPPORTED_BY]->(f)
WITH h
UNWIND $contra AS fid2
MATCH (f2:Finding {id: fid2})
MERGE (h)-[:CONTRADICTED_BY]->(f2)
RETURN h.id AS hypothesis
"""

RECORD_ACTION = """
MATCH (run:AgentRun {id: $run_id})
CREATE (a:ActionRecord {id: $id, kind: $kind, target: $target, rationale: $rationale, path: $path,
                        rejected_alternative: $rejected, rejected_reason: $rejected_reason, ts: $ts, agent: $agent})
CREATE (run)-[:TOOK]->(a)
WITH a
UNWIND $evidence AS eid
MATCH (n {id: eid})
CREATE (a)-[:JUSTIFIED_BY]->(n)
RETURN a.id AS action, count(n) AS evidence_links
"""
