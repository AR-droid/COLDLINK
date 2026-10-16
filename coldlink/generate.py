"""Synthetic data generator for the Southern Regional Forensic Lab (SRFL).

Everything here is invented. No real person, victim, case or place is used.
People connected to cases appear only as opaque subject IDs (P-xxxx); the
agent never names a suspect.

The generator writes data/srfl.json with about 3,000 cases (2019-2026) and
about 9,000 exhibits, and plants four stories the agent must find:

1. The window series: seven burglaries across four stations share a 14 mm
   flat-chamfered pry-bar mark, a zigzag sole and a rear first-floor entry
   between 01:00 and 04:00. Humans linked only two of them (S6 and S7).
2. The undermined alibi: in 2023 (S5) a subject was eliminated by CCTV from
   camera CAM-17. A 2025 case found CAM-17's clock runs 47 minutes fast.
3. The decoy: a burglary with a similar 14 mm pry-bar mark but a serrated
   edge profile and a daytime front-door entry.
4. The custody gap: S1's footwear lift has a 9-day hole in its custody chain.

Two extra plants make the agent's choices non-trivial:
- A false footwear link (BG1) examined under a superseded SOP. A re-test
  disproves it, which exercises memory revision.
- A second CAM-17 decision whose margin (120 min) survives the 47-minute
  correction, so the agent must check margins rather than reopen everything.

Usage: python -m coldlink.generate [--seed 7] [--cases 3000] [--out data/srfl.json]
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

LAB_ID = "srfl"
LAB_NAME = "Southern Regional Forensic Lab"

STATIONS = [
    ("ST-NG", "Northgate"), ("ST-RV", "Riverside"), ("ST-EF", "Eastfield"),
    ("ST-HB", "Harbour"), ("ST-MB", "Millbrook"), ("ST-WC", "Westcombe"),
    ("ST-KW", "Kingsway"), ("ST-AV", "Ashford Vale"),
]

CASE_TYPES = [
    ("burglary", 0.42), ("theft", 0.16), ("criminal_damage", 0.12),
    ("vehicle_crime", 0.12), ("robbery", 0.08), ("assault", 0.10),
]

ENTRY_POINTS = [
    "rear first-floor window", "rear ground-floor window", "rear ground-floor door",
    "front door", "side door", "garage door", "front ground-floor window",
]

TM_CLASSES = ["pry-bar", "screwdriver", "chisel", "bolt-cutter", "wrecking-bar", "tyre-lever"]
TM_PROFILES = ["flat-chamfered", "flat-square", "rounded", "serrated", "bevelled"]
FW_PATTERNS = ["zigzag", "hexagonal", "bar", "circular", "wavy", "block", "chevron", "lug",
               "herringbone", "diamond", "star", "target", "multi-bar", "dimple"]
VEHICLES = [
    ("Ford Transit", "white"), ("Vauxhall Corsa", "silver"), ("VW Golf", "black"),
    ("Ford Focus", "blue"), ("Toyota Yaris", "red"), ("Renault Kangoo", "white"),
    ("Nissan Qashqai", "grey"), ("Citroen Berlingo", "white"),
]

METHODS = [
    ("M-TM", "Tool mark comparison microscopy", "toolmark"),
    ("M-FW", "Footwear impression comparison", "footwear"),
    ("M-VEH", "Vehicle identification from sightings", "vehicle"),
    ("M-CCTV", "CCTV review and timeline", "cctv"),
    ("M-DNA", "DNA profiling", "dna"),
    ("M-FP", "Fingerprint comparison", "fingerprint"),
    ("M-FIB", "Fibre analysis", "fibre"),
    ("M-GLS", "Glass fragment analysis", "glass"),
]

# SOPs. Superseded ones carry valid_to and a SUPERSEDES edge from the newer one.
SOPS = [
    ("SOP-TM-1", "M-TM", 1, "2015-01-01", None, "Tool mark casting and comparison"),
    ("SOP-FW-2", "M-FW", 2, "2016-06-01", "2022-03-01",
     "Footwear comparison by pattern class and size only"),
    ("SOP-FW-3", "M-FW", 3, "2022-03-01", None,
     "Footwear comparison by pattern, size and pattern sub-type with wear features"),
    ("SOP-VEH-1", "M-VEH", 1, "2015-01-01", None, "Vehicle identification"),
    ("SOP-CCTV-1", "M-CCTV", 1, "2015-01-01", "2025-09-01",
     "CCTV review using recorder timestamps as given"),
    ("SOP-CCTV-2", "M-CCTV", 2, "2025-09-01", None,
     "CCTV review with mandatory recorder clock verification against a reference"),
    ("SOP-DNA-4", "M-DNA", 4, "2015-01-01", None, "DNA profiling"),
    ("SOP-FP-2", "M-FP", 2, "2015-01-01", None, "Fingerprint comparison"),
    ("SOP-FIB-1", "M-FIB", 1, "2015-01-01", None, "Fibre analysis"),
    ("SOP-GLS-1", "M-GLS", 1, "2015-01-01", None, "Glass analysis"),
]
SUPERSEDES = [("SOP-FW-3", "SOP-FW-2"), ("SOP-CCTV-2", "SOP-CCTV-1")]

FIRST = ["A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "J.", "K.", "L.", "M.", "N.", "P.", "R.", "S.", "T."]
LAST = ["Okafor", "Lindqvist", "Marsh", "Patel", "Novak", "Duarte", "Ferris", "Hale",
        "Iyer", "Quinn", "Abara", "Sato", "Brennan", "Kowal", "Mensah", "Varga",
        "Thorne", "Castell", "Rahim", "Ellery"]

EXHIBIT_TYPE_WEIGHTS = {
    "burglary": [("toolmark", 0.55), ("footwear", 0.5), ("fingerprint", 0.35), ("dna", 0.25),
                 ("glass", 0.2), ("fibre", 0.15), ("cctv", 0.2), ("vehicle", 0.15)],
    "theft": [("cctv", 0.5), ("fingerprint", 0.4), ("dna", 0.2), ("vehicle", 0.2)],
    "criminal_damage": [("toolmark", 0.3), ("footwear", 0.3), ("glass", 0.3), ("cctv", 0.3)],
    "vehicle_crime": [("toolmark", 0.4), ("glass", 0.4), ("fingerprint", 0.4), ("cctv", 0.3), ("vehicle", 0.3)],
    "robbery": [("cctv", 0.6), ("dna", 0.4), ("vehicle", 0.4), ("fibre", 0.3), ("footwear", 0.3)],
    "assault": [("dna", 0.6), ("cctv", 0.4), ("fibre", 0.4), ("footwear", 0.2)],
}
METHOD_FOR = {m[2]: m[0] for m in METHODS}


DATA_CUTOFF = datetime(2026, 10, 1, 17, 0)  # nothing in the history is later than this


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class Gen:
    def __init__(self, seed: int, n_cases: int):
        self.r = random.Random(seed)
        self.n_cases = n_cases
        self.cases, self.exhibits, self.findings = [], [], []
        self.decisions, self.custody, self.statements = [], [], []
        self.locations, self.cameras, self.examiners = [], [], []
        self.case_seq: dict[int, int] = {}
        self.ex_seq = 0
        self.f_seq = 0
        self.d_seq = 0
        self.st_seq = 0
        self.reserved: set[str] = set()
        self.hidden_subtype: dict[str, str] = {}

    # ---- ids -------------------------------------------------------------
    def case_id(self, year: int) -> str:
        while True:
            self.case_seq[year] = self.case_seq.get(year, 0) + 1
            cid = f"SRFL-{year}-{self.case_seq[year]:04d}"
            if cid not in self.reserved:
                return cid

    def ex_id(self) -> str:
        self.ex_seq += 1
        return f"EX-{self.ex_seq:05d}"

    def f_id(self) -> str:
        self.f_seq += 1
        return f"F-{self.f_seq:05d}"

    def d_id(self) -> str:
        self.d_seq += 1
        return f"D-{self.d_seq:05d}"

    def s_id(self) -> str:
        self.st_seq += 1
        return f"STM-{self.st_seq:05d}"

    # ---- reference data ----------------------------------------------------
    def build_reference(self):
        r = self.r
        for sid, _ in STATIONS:
            for k in range(6):
                self.locations.append({"id": f"LOC-{sid[3:]}-{k + 1:02d}", "station": sid,
                                       "district": f"{sid[3:]} district {k + 1}"})
        cam_n = 0
        for sid, _ in STATIONS:
            for _ in range(5):
                cam_n += 1
                self.cameras.append({"id": f"CAM-{cam_n:02d}", "station": sid,
                                     "site": f"{r.choice(['Petrol station', 'Car park', 'High street', 'Ring road', 'Retail park'])} {cam_n}",
                                     "owner": r.choice(["council", "private", "transport"])})
        # CAM-17 is the planted camera: a petrol station forecourt camera.
        cam17 = next(c for c in self.cameras if c["id"] == "CAM-17")
        cam17.update({"site": "Petrol station forecourt, Harbour Road", "owner": "private"})
        names = set()
        for i in range(40):
            while True:
                n = f"{r.choice(FIRST)} {r.choice(LAST)}"
                if n not in names:
                    names.add(n)
                    break
            start = datetime(r.randint(2008, 2022), r.randint(1, 12), 1)
            left = None
            if r.random() < 0.25 and start.year < 2023:
                left = datetime(r.randint(max(start.year + 2, 2020), 2026), r.randint(1, 9), 1)
            specs = r.sample([m[0] for m in METHODS], k=r.choice([1, 2, 2, 3]))
            self.examiners.append({"id": f"EXM-{i + 1:02d}", "name": n, "methods": specs,
                                   "start": iso(start), "left": iso(left) if left else None})
        # Make sure the planted re-test has a clear best examiner who is still active.
        e = self.examiners[7]
        e.update({"methods": ["M-FW", "M-TM"], "start": "2014-02-01T00:00:00", "left": None})
        e2 = self.examiners[12]  # experienced footwear examiner who has left
        e2.update({"methods": ["M-FW"], "start": "2009-05-01T00:00:00", "left": "2024-06-01T00:00:00"})

    def examiner_for(self, method: str, when: datetime) -> str:
        pool = [e for e in self.examiners if method in e["methods"]
                and e["start"] <= iso(when) and (e["left"] is None or e["left"] > iso(when))]
        if not pool:
            pool = [e for e in self.examiners if e["start"] <= iso(when)
                    and (e["left"] is None or e["left"] > iso(when))] or self.examiners
        return self.r.choice(pool)["id"]

    @staticmethod
    def sop_for(method: str, when: datetime) -> str:
        w = iso(when)[:10]
        for sid, m, _, frm, to, _ in SOPS:
            if m == method and frm <= w and (to is None or w < to):
                return sid
        return next(s[0] for s in SOPS if s[1] == method)

    # ---- per-exhibit records ---------------------------------------------
    def add_custody(self, ex_id: str, collected: datetime, examined: datetime, gap_days: int | None = None):
        r = self.r
        t = collected
        ev = [("collected", "scene", t)]
        t += timedelta(hours=r.randint(1, 6)); ev.append(("sealed", "scene", t))
        t += timedelta(hours=r.randint(2, 30)); ev.append(("stored", "station store", t))
        t += timedelta(days=r.randint(1, 20)); ev.append(("dispatched", "station store", t))
        if gap_days:
            t += timedelta(days=gap_days)
        else:
            t += timedelta(hours=r.randint(2, 48))
        ev.append(("received", "lab intake", t))
        t = max(t + timedelta(hours=2), examined); ev.append(("examined", "lab", t))
        t += timedelta(days=r.randint(1, 10)); ev.append(("stored", "lab store", t))
        for i, (action, place, ts) in enumerate(ev):
            self.custody.append({"id": f"CE-{ex_id[3:]}-{i + 1}", "exhibit": ex_id, "seq": i + 1,
                                 "action": action, "place": place, "ts": iso(ts)})

    def add_exhibit(self, case: dict, etype: str, props: dict | None = None, when: datetime | None = None,
                    examiner: str | None = None, sop: str | None = None, finding_text: str | None = None,
                    gap_days: int | None = None, fid_out: list | None = None) -> dict:
        r = self.r
        cdt = datetime.fromisoformat(case["date"])
        ex = {"id": self.ex_id(), "case": case["id"], "type": etype, "collected": iso(cdt + timedelta(hours=r.randint(2, 12)))}
        ex.update(props if props is not None else self.random_props(etype, cdt))
        self.exhibits.append(ex)
        method = METHOD_FOR[etype]
        examined = min(when or cdt + timedelta(days=r.randint(5, 60)), DATA_CUTOFF)
        exm = examiner or self.examiner_for(method, examined)
        sop = sop or self.sop_for(method, examined)
        if etype == "footwear" and sop == "SOP-FW-2":
            # The old SOP did not record the pattern sub-type. Keep the true value
            # hidden so a simulated re-test under SOP-FW-3 can reveal it.
            self.hidden_subtype[ex["id"]] = ex["subtype"]
            ex["subtype"] = None
        fid = self.f_id()
        self.findings.append({"id": fid, "about": ex["id"], "about_kind": "Exhibit", "date": iso(examined),
                              "examiner": exm, "method": method, "sop": sop,
                              "kind": f"{etype}_examination",
                              "text": finding_text or self.finding_text(ex)})
        if fid_out is not None:
            fid_out.append(fid)
        self.add_custody(ex["id"], datetime.fromisoformat(ex["collected"]), examined, gap_days)
        return ex

    def random_props(self, etype: str, when: datetime) -> dict:
        r = self.r
        if etype == "toolmark":
            while True:
                p = {"tm_class": r.choice(TM_CLASSES), "width_mm": round(r.uniform(4, 32), 1),
                     "edge_profile": r.choice(TM_PROFILES)}
                # Keep the series signature out of the background (planted cases only).
                if not (p["tm_class"] == "pry-bar" and 13.0 <= p["width_mm"] <= 15.0):
                    return p
        if etype == "footwear":
            while True:
                p = {"pattern": r.choice(FW_PATTERNS), "size": r.randint(3, 13),
                     "subtype": r.choice(["a", "b", "c"]), "wear_code": f"W-{r.randint(1, 40):02d}"}
                if not (p["pattern"] == "zigzag" and p["size"] in (9, 10)):
                    return p
        if etype == "vehicle":
            mm, col = r.choice(VEHICLES)
            if mm == "Ford Transit":
                mm, col = "Ford Transit", r.choice(["blue", "silver", "red"])
            return {"make_model": mm, "colour": col,
                    "plate_partial": f"{r.choice('ABCDEFGHJKLMNPRSTVWXY')}{r.choice('ABCDEFGHJKLMNPRSTVWXY')}{r.randint(10, 79)}"}
        if etype == "cctv":
            cam = r.choice(self.cameras)
            return {"camera": cam["id"], "recorded": iso(when + timedelta(minutes=r.randint(-120, 120)))}
        return {"detail": f"{etype} sample {r.randint(1, 999)}"}

    @staticmethod
    def finding_text(ex: dict) -> str:
        t = ex["type"]
        if t == "toolmark":
            return f"{ex['tm_class']} mark, blade width {ex['width_mm']} mm, {ex['edge_profile']} edge"
        if t == "footwear":
            sub = f"sub-type {ex['subtype']}" if ex.get("subtype") else "sub-type not recorded"
            return f"{ex['pattern']} sole pattern, size {ex['size']}, {sub}, wear {ex['wear_code']}"
        if t == "vehicle":
            return f"Witness sighting of {ex['colour']} {ex['make_model']}, partial plate {ex['plate_partial']}"
        if t == "cctv":
            return f"Recording from {ex['camera']} reviewed; timestamp {ex['recorded']}"
        return f"{t} examined; no comparison result recorded"

    # ---- cases -----------------------------------------------------------
    def new_case(self, cid: str, when: datetime, station: str, ctype: str, entry: str,
                 status: str = "unsolved", **extra) -> dict:
        locs = [l for l in self.locations if l["station"] == station]
        c = {"id": cid, "year": when.year, "date": iso(when), "station": station, "type": ctype,
             "entry_point": entry, "hour": when.hour, "location": self.r.choice(locs)["id"],
             "status": status, "sealed": False, "planted": None}
        c.update(extra)
        self.cases.append(c)
        return c

    def background(self, n: int):
        r = self.r
        types, weights = zip(*CASE_TYPES)
        for _ in range(n):
            year = r.choice([2019, 2020, 2021, 2022, 2023, 2024, 2025, 2025, 2026])
            when = datetime(year, r.randint(1, 8 if year == 2026 else 12), r.randint(1, 28),
                            r.randint(0, 23), r.choice([0, 10, 20, 30, 40, 50]))
            ctype = r.choices(types, weights)[0]
            entry = r.choice(ENTRY_POINTS) if ctype in ("burglary", "criminal_damage") else None
            # Background burglaries with the series MO at night are rare but allowed.
            c = self.new_case(self.case_id(year), when, r.choice(STATIONS)[0], ctype, entry,
                              status=r.choices(["closed", "unsolved", "open"], [0.55, 0.35, 0.1])[0])
            n_ex = 0
            for etype, p in EXHIBIT_TYPE_WEIGHTS[ctype]:
                if r.random() < p:
                    self.add_exhibit(c, etype, when=when + timedelta(days=r.randint(3, 90)))
                    n_ex += 1
            while n_ex < 3:
                etype = r.choice(EXHIBIT_TYPE_WEIGHTS[ctype])[0]
                self.add_exhibit(c, etype, when=when + timedelta(days=r.randint(3, 90)))
                n_ex += 1
            if r.random() < 0.3:
                self.add_statement(c, r.choice(["Neighbour heard glass breaking.",
                                                "Witness saw a person leaving on foot.",
                                                "Owner reports nothing seen.",
                                                "Witness saw a car parked nearby."]))
            if c["status"] == "closed" and r.random() < 0.5:
                fids = [f["id"] for f in self.findings if f["about"] in
                        {e["id"] for e in self.exhibits[-n_ex:]}]
                self.decisions.append({"id": self.d_id(), "case": c["id"],
                                       "date": iso(when + timedelta(days=r.randint(60, 200))),
                                       "kind": "no_further_action", "status": "active",
                                       "text": "No further forensic action: no comparison leads.",
                                       "based_on": fids[:2], "margin_minutes": None})

    def add_statement(self, case: dict, text: str, mentions: dict | None = None):
        self.statements.append({"id": self.s_id(), "case": case["id"], "date": case["date"],
                                "witness": f"W-{self.r.randint(1000, 9999)}", "text": text,
                                "mentions": mentions or {}})

    # ---- planted story ---------------------------------------------------
    def plant(self) -> dict:
        r = self.r
        gt: dict = {"series": [], "human_linked": [], "decoy": None, "false_link_case": None,
                    "alibi_decision": None, "unaffected_cam17_decision": None,
                    "camera_finding": None, "custody_gap_exhibit": None, "retest_outcomes": {}}

        def tm(width, profile="flat-chamfered"):
            return {"tm_class": "pry-bar", "width_mm": width, "edge_profile": profile}

        def fw(size=9, subtype="b", wear="W-07"):
            return {"pattern": "zigzag", "size": size, "subtype": subtype, "wear_code": wear}

        van = lambda plate: {"make_model": "Ford Transit", "colour": "white", "plate_partial": plate}
        entry = "rear first-floor window"
        plan = [
            # key, case id, datetime, station, exhibits
            ("S1", "SRFL-2020-0112", datetime(2020, 3, 14, 2, 20), "ST-NG", [("toolmark", tm(14.0)), ("footwear_gap", fw(10, "a", "W-17"))]),
            ("S2", "SRFL-2021-0187", datetime(2021, 6, 2, 3, 10), "ST-RV", [("footwear", fw()), ("vehicle", van("KX6"))]),
            ("S3", "SRFL-2021-0301", datetime(2021, 11, 20, 1, 40), "ST-NG", [("toolmark", tm(14.1)), ("vehicle", van("KX6"))]),
            ("S4", "SRFL-2022-0091", datetime(2022, 4, 9, 2, 50), "ST-EF", [("toolmark", tm(13.9)), ("footwear", fw())]),
            ("S5", "SRFL-2023-0155", datetime(2023, 5, 27, 2, 30), "ST-HB", [("toolmark", tm(14.0)), ("footwear", fw()), ("cctv_alibi", None)]),
            ("S6", "SRFL-2024-0058", datetime(2024, 2, 18, 3, 25), "ST-RV", [("toolmark", tm(14.1)), ("footwear", fw())]),
            ("S7", "SRFL-2025-0139", datetime(2025, 7, 6, 1, 15), "ST-EF", [("toolmark", tm(13.9)), ("vehicle", van("KX6"))]),
        ]
        for _, cid, *_ in plan:
            self.reserved.add(cid)
        for cid in ["SRFL-2024-0277", "SRFL-2021-0264", "SRFL-2025-0302", "SRFL-2022-0210"]:
            self.reserved.add(cid)

        fw_examiner_old = self.examiners[12]["id"]  # has since left
        series_cases = {}
        for key, cid, when, st, exs in plan:
            c = self.new_case(cid, when, st, "burglary", entry, planted=f"series:{key}")
            series_cases[key] = c
            gt["series"].append(cid)
            for etype, props in exs:
                exd = when + timedelta(days=r.randint(7, 30))
                if etype == "footwear_gap":
                    ex = self.add_exhibit(c, "footwear", props, when=exd, examiner=fw_examiner_old, gap_days=9)
                    gt["custody_gap_exhibit"] = ex["id"]
                elif etype == "cctv_alibi":
                    continue
                elif key == "S2" and etype == "footwear":
                    # Backlogged: examined after SOP-FW-3 came in, so the sub-type is recorded.
                    self.add_exhibit(c, "footwear", props, when=datetime(2022, 3, 15, 10, 0))
                elif etype == "footwear" and when.year < 2022:
                    self.add_exhibit(c, "footwear", props, when=exd, examiner=fw_examiner_old)
                else:
                    self.add_exhibit(c, etype, props, when=exd)
            if key in ("S2", "S3", "S7"):
                self.add_statement(c, "Witness saw a white van with its lights off in the rear lane around the time of the break-in.",
                                   {"vehicle": "white Ford Transit", "plate_partial": "KX6"})
            else:
                self.add_statement(c, "Occupier asleep; entry through rear upstairs window found in the morning.")

        # Humans linked only S6 and S7 (2025) - an accepted link decision and a pattern.
        s6, s7 = series_cases["S6"], series_cases["S7"]
        tm6 = next(f["id"] for f in self.findings if f["about"] in {e["id"] for e in self.exhibits if e["case"] == s6["id"] and e["type"] == "toolmark"})
        tm7 = next(f["id"] for f in self.findings if f["about"] in {e["id"] for e in self.exhibits if e["case"] == s7["id"] and e["type"] == "toolmark"})
        did = self.d_id()
        self.decisions.append({"id": did, "case": s7["id"], "date": "2025-08-20T10:00:00", "kind": "link",
                               "status": "active", "linked_case": s6["id"],
                               "text": "Linked to SRFL-2024-0058 on matching 14 mm pry-bar marks.",
                               "based_on": [tm6, tm7], "margin_minutes": None})
        gt["human_linked"] = [s6["id"], s7["id"]]
        gt["human_link_decision"] = did

        # The undermined alibi (S5, 2023). Subject P-0193 eliminated by CAM-17.
        s5 = series_cases["S5"]
        cctv = {"camera": "CAM-17", "recorded": "2023-05-27T02:05:00"}
        fids: list = []
        self.add_exhibit(s5, "cctv", cctv, when=datetime(2023, 6, 3, 11, 0), sop="SOP-CCTV-1", fid_out=fids,
                         finding_text="CAM-17 recording shows subject P-0193 on the petrol station forecourt at 02:05 (recorder time).")
        did = self.d_id()
        self.decisions.append({"id": did, "case": s5["id"], "date": "2023-06-10T15:00:00", "kind": "eliminate_subject",
                               "status": "active", "subject": "P-0193",
                               "text": ("Subject P-0193 eliminated: CAM-17 places the subject 25 minutes' drive from the scene at 02:05; "
                                        "entry estimated 02:20-02:40, so the subject could not have arrived in time."),
                               "based_on": fids, "margin_minutes": 25,
                               "time_claim": "2023-05-27T02:05:00"})
        gt["alibi_decision"] = did

        # A second CAM-17 decision with a margin that survives a 47-minute shift.
        other = self.new_case("SRFL-2022-0210", datetime(2022, 9, 3, 22, 40), "ST-HB", "theft", None, status="closed")
        fids = []
        self.add_exhibit(other, "cctv", {"camera": "CAM-17", "recorded": "2022-09-03T20:30:00"},
                         when=datetime(2022, 9, 10, 10, 0), sop="SOP-CCTV-1", fid_out=fids,
                         finding_text="CAM-17 shows subject P-0548 at the forecourt at 20:30 (recorder time).")
        did = self.d_id()
        self.decisions.append({"id": did, "case": other["id"], "date": "2022-09-20T12:00:00", "kind": "eliminate_subject",
                               "status": "active", "subject": "P-0548",
                               "text": "Subject P-0548 eliminated: on camera more than two hours before the theft.",
                               "based_on": fids, "margin_minutes": 130, "time_claim": "2022-09-03T20:30:00"})
        gt["unaffected_cam17_decision"] = did

        # The 2025 camera-clock finding, made in an unrelated traffic case.
        cam_case = self.new_case("SRFL-2025-0302", datetime(2025, 10, 2, 17, 5), "ST-HB", "vehicle_crime", None, status="closed")
        self.add_exhibit(cam_case, "cctv", {"camera": "CAM-17", "recorded": "2025-10-02T17:52:00"},
                         when=datetime(2025, 10, 9, 9, 0), sop="SOP-CCTV-2")
        fid = self.f_id()
        self.findings.append({"id": fid, "about": "CAM-17", "about_kind": "Camera", "date": "2025-10-09T14:00:00",
                              "examiner": self.examiner_for("M-CCTV", datetime(2025, 10, 9)), "method": "M-CCTV",
                              "sop": "SOP-CCTV-2", "kind": "clock_verification", "clock_offset_minutes": 47,
                              "text": "CAM-17 recorder clock verified against reference: runs 47 minutes fast."})
        gt["camera_finding"] = fid

        # The decoy: similar width, serrated edge, daytime front-door entry.
        decoy = self.new_case("SRFL-2024-0277", datetime(2024, 9, 12, 14, 10), "ST-HB", "burglary", "front door")
        self.add_exhibit(decoy, "toolmark", tm(14.2, "serrated"))
        self.add_exhibit(decoy, "fingerprint")
        gt["decoy"] = decoy["id"]

        # A false footwear link examined under the superseded SOP-FW-2.
        bg = self.new_case("SRFL-2021-0264", datetime(2021, 12, 11, 23, 30), "ST-WC", "burglary", entry)
        ex = self.add_exhibit(bg, "footwear", fw(9, "c", "W-07"), when=datetime(2021, 12, 20, 10, 0), examiner=fw_examiner_old)
        self.add_exhibit(bg, "glass")
        gt["false_link_case"] = bg["id"]
        return gt

    def renumber(self, gt: dict):
        """Re-issue exhibit, finding and decision ids in date order so planted
        records do not stand out by having the lowest ids."""
        ex_map = {e["id"]: f"EX-{i + 1:05d}" for i, e in enumerate(sorted(self.exhibits, key=lambda e: (e["collected"], e["id"])))}
        f_map = {f["id"]: f"F-{i + 1:05d}" for i, f in enumerate(sorted(self.findings, key=lambda f: (f["date"], f["id"])))}
        d_map = {d["id"]: f"D-{i + 1:05d}" for i, d in enumerate(sorted(self.decisions, key=lambda d: (d["date"], d["id"])))}
        for e in self.exhibits:
            e["id"] = ex_map[e["id"]]
        for f in self.findings:
            f["id"] = f_map[f["id"]]
            if f["about_kind"] == "Exhibit":
                f["about"] = ex_map[f["about"]]
        for d in self.decisions:
            d["id"] = d_map[d["id"]]
            d["based_on"] = [f_map[x] for x in d["based_on"]]
        for c in self.custody:
            c["exhibit"] = ex_map[c["exhibit"]]
            c["id"] = f"CE-{c['exhibit'][3:]}-{c['seq']}"
        self.exhibits.sort(key=lambda e: e["id"]); self.findings.sort(key=lambda f: f["id"])
        self.decisions.sort(key=lambda d: d["id"])
        for k in ("alibi_decision", "unaffected_cam17_decision", "human_link_decision"):
            gt[k] = d_map[gt[k]]
        gt["camera_finding"] = f_map[gt["camera_finding"]]
        gt["custody_gap_exhibit"] = ex_map[gt["custody_gap_exhibit"]]
        gt["retest_outcomes"] = {ex_map[k]: {"subtype": v} for k, v in self.hidden_subtype.items()}

    def run(self) -> dict:
        self.build_reference()
        gt = self.plant()
        self.background(self.n_cases - len(self.cases))
        self.renumber(gt)
        self.cases.sort(key=lambda c: c["date"])
        return {
            "lab": {"id": LAB_ID, "name": LAB_NAME},
            "stations": [{"id": s, "name": n} for s, n in STATIONS],
            "locations": self.locations, "cameras": self.cameras, "examiners": self.examiners,
            "methods": [{"id": m, "name": n, "evidence_type": t} for m, n, t in METHODS],
            "sops": [{"id": s, "method": m, "version": v, "valid_from": f, "valid_to": t, "title": ti}
                     for s, m, v, f, t, ti in SOPS],
            "supersedes": [{"newer": a, "older": b} for a, b in SUPERSEDES],
            "cases": self.cases, "exhibits": self.exhibits, "findings": self.findings,
            "decisions": self.decisions, "custody": self.custody, "statements": self.statements,
            "ground_truth": gt,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--out", default="data/srfl.json")
    a = ap.parse_args()
    data = Gen(a.seed, a.cases).run()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    print(f"wrote {out}: {len(data['cases'])} cases, {len(data['exhibits'])} exhibits, "
          f"{len(data['findings'])} findings, {len(data['decisions'])} decisions, "
          f"{len(data['custody'])} custody events, {len(data['examiners'])} examiners")


if __name__ == "__main__":
    main()
