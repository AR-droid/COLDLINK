import { useEffect, useState, type FormEvent } from "react";
import { api, type CaseIn, type ExhibitIn, type Reference } from "../api";

function emptyExhibit(): ExhibitIn {
  return { type: "toolmark" };
}

function defaultDate(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function NewCase({ onSubmit }: { onSubmit: (body: CaseIn) => void }) {
  const [ref, setRef] = useState<Reference | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [station, setStation] = useState("");
  const [location, setLocation] = useState("");
  const [date, setDate] = useState(defaultDate());
  const [type, setType] = useState("burglary");
  const [entryPoint, setEntryPoint] = useState("");
  const [exhibits, setExhibits] = useState<ExhibitIn[]>([emptyExhibit()]);

  useEffect(() => {
    api
      .reference()
      .then((r) => {
        setRef(r);
        if (r.stations[0]) setStation(r.stations[0].id);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const locationsForStation = ref?.locations.filter((l) => l.station === station) || [];

  function updateExhibit(i: number, patch: Partial<ExhibitIn>) {
    setExhibits((xs) => xs.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const body: CaseIn = {
      station,
      location: location || undefined,
      date: date.length === 16 ? `${date}:00` : date,
      type,
      entry_point: entryPoint || undefined,
      exhibits,
    };
    onSubmit(body);
  }

  if (err) return <div className="network-err">{err}</div>;
  if (!ref) return <div className="muted">Loading form&hellip;</div>;

  return (
    <div className="newcase">
      <div className="dash-head">
        <h1>File a New Case</h1>
        <p className="dash-sub">
          Enter a real case. On submit it is ingested into the live graph and the Lead agent
          investigates it immediately &mdash; the result is whatever the graph actually supports,
          not a script.
        </p>
      </div>

      <form className="panel case-form" onSubmit={submit}>
        <div className="form-row">
          <label>
            Station
            <select value={station} onChange={(e) => { setStation(e.target.value); setLocation(""); }}>
              {ref.stations.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
          <label>
            Location
            <select value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">(any at this station)</option>
              {locationsForStation.map((l) => (
                <option key={l.id} value={l.id}>{l.id}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="form-row">
          <label>
            Date &amp; time
            <input type="datetime-local" value={date} onChange={(e) => setDate(e.target.value)} required />
          </label>
          <label>
            Case type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {ref.case_types.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            Entry point
            <select value={entryPoint} onChange={(e) => setEntryPoint(e.target.value)}>
              <option value="">(none)</option>
              {ref.entry_points.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="panel-head form-subhead">
          <span>Exhibits</span>
          <span className="panel-note">at least one, matched against the real graph</span>
        </div>

        {exhibits.map((ex, i) => (
          <ExhibitFields
            key={i}
            ex={ex}
            ref_={ref}
            onChange={(patch) => updateExhibit(i, patch)}
            onRemove={exhibits.length > 1 ? () => setExhibits((xs) => xs.filter((_, idx) => idx !== i)) : undefined}
          />
        ))}
        <button type="button" className="ghost add-exhibit" onClick={() => setExhibits((xs) => [...xs, emptyExhibit()])}>
          + Add exhibit
        </button>

        <div className="form-actions">
          <button type="submit" className="primary">Submit &amp; investigate</button>
        </div>
      </form>
    </div>
  );
}

function ExhibitFields({
  ex,
  ref_,
  onChange,
  onRemove,
}: {
  ex: ExhibitIn;
  ref_: Reference;
  onChange: (patch: Partial<ExhibitIn>) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="exhibit-block">
      <div className="form-row">
        <label>
          Type
          <select value={ex.type} onChange={(e) => onChange({ type: e.target.value as ExhibitIn["type"] })}>
            <option value="toolmark">toolmark</option>
            <option value="footwear">footwear</option>
            <option value="vehicle">vehicle</option>
          </select>
        </label>
        {onRemove && (
          <button type="button" className="ghost remove-exhibit" onClick={onRemove}>Remove</button>
        )}
      </div>

      {ex.type === "toolmark" && (
        <div className="form-row">
          <label>
            Class
            <select value={ex.tm_class || ""} onChange={(e) => onChange({ tm_class: e.target.value })}>
              <option value="">choose&hellip;</option>
              {ref_.exhibit_types.toolmark.tm_class.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label>
            Width (mm)
            <input
              type="number"
              step="0.1"
              value={ex.width_mm ?? ""}
              onChange={(e) => onChange({ width_mm: e.target.value ? parseFloat(e.target.value) : undefined })}
            />
          </label>
          <label>
            Edge profile
            <select value={ex.edge_profile || ""} onChange={(e) => onChange({ edge_profile: e.target.value })}>
              <option value="">choose&hellip;</option>
              {ref_.exhibit_types.toolmark.edge_profile.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        </div>
      )}

      {ex.type === "footwear" && (
        <div className="form-row">
          <label>
            Pattern
            <select value={ex.pattern || ""} onChange={(e) => onChange({ pattern: e.target.value })}>
              <option value="">choose&hellip;</option>
              {ref_.exhibit_types.footwear.pattern.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>
            Size
            <input
              type="number"
              value={ex.size ?? ""}
              onChange={(e) => onChange({ size: e.target.value ? parseInt(e.target.value) : undefined })}
            />
          </label>
          <label>
            Sub-type
            <select value={ex.subtype || ""} onChange={(e) => onChange({ subtype: e.target.value })}>
              <option value="">not recorded</option>
              {ref_.exhibit_types.footwear.subtype.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>
            Wear code
            <input placeholder="W-07" value={ex.wear_code || ""} onChange={(e) => onChange({ wear_code: e.target.value })} />
          </label>
        </div>
      )}

      {ex.type === "vehicle" && (
        <div className="form-row">
          <label>
            Make / model
            <input placeholder="Ford Transit" value={ex.make_model || ""} onChange={(e) => onChange({ make_model: e.target.value })} />
          </label>
          <label>
            Colour
            <input placeholder="white" value={ex.colour || ""} onChange={(e) => onChange({ colour: e.target.value })} />
          </label>
          <label>
            Partial plate
            <input placeholder="KX63..." value={ex.plate_partial || ""} onChange={(e) => onChange({ plate_partial: e.target.value })} />
          </label>
        </div>
      )}
    </div>
  );
}
