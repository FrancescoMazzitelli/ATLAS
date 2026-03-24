import os
import zipfile
import tempfile
import json
import shapefile
import psycopg2
from psycopg2 import sql
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

def _load_databases():
    raw = os.environ.get('DB_CONNECTIONS')
    if raw:
        return json.loads(raw)

DATABASES = _load_databases()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn(label=None):
    cfg = DATABASES.get(label) or next(iter(DATABASES.values()))
    return psycopg2.connect(
        host=cfg['host'], port=cfg['port'], dbname=cfg['dbname'],
        user=cfg['user'], password=cfg['password'],
    )

def get_schemas(label=None):
    try:
        conn = get_conn(label)
        cur  = conn.cursor()
        cur.execute("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
            ORDER BY schema_name
        """)
        schemas = [r[0] for r in cur.fetchall()]
        conn.close()
        return schemas
    except Exception:
        return ['public']


# ---------------------------------------------------------------------------
# Shapefile helpers
# ---------------------------------------------------------------------------

SHAPE_TYPES = {
    0:'Null', 1:'Point', 3:'Polyline', 5:'Polygon',
    8:'MultiPoint', 11:'PointZ', 13:'PolylineZ', 15:'PolygonZ',
    18:'MultiPointZ', 21:'PointM', 23:'PolylineM', 25:'PolygonM',
    28:'MultiPointM', 31:'MultiPatch',
}

def find_file(directory, extension):
    for root, _, files in os.walk(directory):
        for fn in files:
            if fn.lower().endswith(extension.lower()):
                return os.path.join(root, fn)
    return None

def detect_srid(prj_path):
    if not prj_path or not os.path.exists(prj_path):
        return None
    c = open(prj_path).read()
    if 'WGS_1984' in c or 'WGS 1984' in c: return 4326
    if 'ETRS' in c:                          return 4258
    if 'Monte_Mario' in c:                   return 3003
    if 'RGF93' in c:                         return 2154
    return None

def geom_to_wkt(shape):
    """Convert a pyshp shape to WKT string."""
    t = shape.shapeType

    # --- Point ---
    if t in (1, 11, 21):
        x, y = shape.points[0]
        return f'POINT({x} {y})'

    # --- MultiPoint ---
    if t in (8, 18, 28):
        pts = ', '.join(f'{x} {y}' for x, y in shape.points)
        return f'MULTIPOINT({pts})'

    # --- Polyline → MULTILINESTRING ---
    if t in (3, 13, 23):
        parts = shape.parts + [len(shape.points)]
        rings = []
        for i in range(len(parts) - 1):
            pts = shape.points[parts[i]:parts[i+1]]
            rings.append('(' + ', '.join(f'{x} {y}' for x, y in pts) + ')')
        return 'MULTILINESTRING(' + ', '.join(rings) + ')'

    # --- Polygon → MULTIPOLYGON ---
    if t in (5, 15, 25):
        parts = shape.parts + [len(shape.points)]
        rings = ['(' + ', '.join(f'{x} {y}' for x, y in shape.points[parts[i]:parts[i+1]]) + ')'
                 for i in range(len(parts) - 1)]
        # First ring = exterior, rest = holes; wrap as single polygon
        if len(rings) == 1:
            return f'MULTIPOLYGON(({rings[0]}))'
        exterior = rings[0]
        holes    = ', '.join(rings[1:])
        return f'MULTIPOLYGON(({exterior}, {holes}))'

    return None  # unsupported / null shape

def pg_type(dbf_type):
    """Map DBF field type to PostgreSQL column type."""
    return {
        'C': 'TEXT',
        'N': 'NUMERIC',
        'F': 'DOUBLE PRECISION',
        'L': 'BOOLEAN',
        'D': 'DATE',
        'M': 'TEXT',
    }.get(dbf_type, 'TEXT')


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shapefile → PostGIS</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:      #0a0a0f;
    --border:  #1e1e2e;
    --accent:  #00ff88;
    --accent2: #0088ff;
    --danger:  #ff4466;
    --text:    #e0e0f0;
    --muted:   #555570;
    --card:    #13131f;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace; min-height:100vh; }
  body::before {
    content:''; position:fixed; inset:0;
    background-image:
      linear-gradient(rgba(0,255,136,.03) 1px,transparent 1px),
      linear-gradient(90deg,rgba(0,255,136,.03) 1px,transparent 1px);
    background-size:40px 40px; pointer-events:none;
  }
  .container { max-width:900px; margin:0 auto; padding:48px 24px; position:relative; }
  header { margin-bottom:48px; }
  .logo { font-family:'Syne',sans-serif; font-size:11px; font-weight:700; letter-spacing:4px; text-transform:uppercase; color:var(--accent); margin-bottom:12px; }
  h1 { font-family:'Syne',sans-serif; font-size:clamp(28px,5vw,48px); font-weight:800; letter-spacing:-1px; }
  h1 span { color:var(--accent); }
  .subtitle { margin-top:10px; color:var(--muted); font-size:13px; }

  .card { background:var(--card); border:1px solid var(--border); border-radius:4px; padding:28px; margin-bottom:20px; transition:border-color .2s; }
  .card:hover { border-color:#2a2a3e; }
  .card-label { font-size:10px; font-weight:700; letter-spacing:3px; text-transform:uppercase; color:var(--accent); margin-bottom:16px; }

  .dropzone { border:1px dashed #2a2a4a; border-radius:4px; padding:48px 24px; text-align:center; cursor:pointer; position:relative; background:rgba(0,255,136,.02); transition:all .2s; }
  .dropzone:hover,.dropzone.drag-over { border-color:var(--accent); background:rgba(0,255,136,.05); }
  .dropzone input[type=file] { position:absolute; inset:0; opacity:0; cursor:pointer; width:100%; height:100%; }
  .drop-icon { font-size:36px; margin-bottom:12px; opacity:.4; }
  .drop-title { font-family:'Syne',sans-serif; font-size:16px; font-weight:700; margin-bottom:6px; }
  .drop-sub { font-size:11px; color:var(--muted); }

  .form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }
  .form-group { display:flex; flex-direction:column; gap:6px; }
  label { font-size:10px; font-weight:600; letter-spacing:2px; text-transform:uppercase; color:var(--muted); }
  input[type=text],select { background:var(--bg); border:1px solid var(--border); border-radius:3px; color:var(--text); font-family:'JetBrains Mono',monospace; font-size:13px; padding:10px 12px; outline:none; transition:border-color .2s; -webkit-appearance:none; }
  input[type=text]:focus,select:focus { border-color:var(--accent); }
  select option { background:#111; }

  .options-row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:20px; align-items:end; }
  .toggle-group { display:flex; gap:6px; }
  .toggle-btn { flex:1; padding:9px 6px; border:1px solid var(--border); background:transparent; color:var(--muted); font-family:'JetBrains Mono',monospace; font-size:11px; cursor:pointer; border-radius:3px; transition:all .2s; white-space:nowrap; }
  .toggle-btn.active { border-color:var(--accent); color:var(--accent); background:rgba(0,255,136,.07); }

  .btn { display:inline-flex; align-items:center; gap:8px; padding:12px 24px; border-radius:3px; font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:600; letter-spacing:1px; cursor:pointer; border:none; transition:all .2s; text-transform:uppercase; }
  .btn-primary { background:var(--accent); color:#000; }
  .btn-primary:hover { background:#00dd77; }
  .btn-primary:disabled { opacity:.3; cursor:not-allowed; }
  .btn-ghost { background:transparent; border:1px solid var(--border); color:var(--muted); }
  .btn-ghost:hover { border-color:var(--accent2); color:var(--accent2); }

  .preview-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:3px; margin-top:12px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { background:#0d0d1a; padding:8px 12px; text-align:left; font-size:10px; letter-spacing:2px; text-transform:uppercase; color:var(--accent); white-space:nowrap; border-bottom:1px solid var(--border); }
  td { padding:8px 12px; border-bottom:1px solid rgba(30,30,46,.5); white-space:nowrap; }
  tr:last-child td { border-bottom:none; }
  tr:hover td { background:rgba(255,255,255,.02); }

  .log { background:var(--bg); border:1px solid var(--border); border-radius:3px; padding:16px; font-size:12px; line-height:1.9; min-height:60px; max-height:220px; overflow-y:auto; }
  .log-line { display:block; }
  .log-line.ok    { color:var(--accent); }
  .log-line.err   { color:var(--danger); }
  .log-line.info  { color:var(--accent2); }
  .log-line.muted { color:var(--muted); }

  .stats { display:flex; gap:20px; flex-wrap:wrap; margin-bottom:14px; }
  .stat  { font-size:12px; color:var(--muted); }
  .stat strong { color:var(--text); }
  .badge { display:inline-block; padding:2px 8px; border-radius:2px; font-size:10px; font-weight:600; letter-spacing:1px; text-transform:uppercase; }
  .badge-green { background:rgba(0,255,136,.1); color:var(--accent); border:1px solid rgba(0,255,136,.2); }
  .badge-blue  { background:rgba(0,136,255,.1); color:var(--accent2); border:1px solid rgba(0,136,255,.2); }

  @keyframes spin { to { transform:rotate(360deg); } }
  .spinner { width:14px; height:14px; border:2px solid rgba(0,255,136,.2); border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; display:inline-block; }

  .hidden { display:none !important; }
  @media(max-width:600px) { .form-row,.options-row { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo">▸ Geo Tools</div>
    <h1>Shapefile <span>→</span> PostGIS</h1>
    <p class="subtitle">Upload a shapefile ZIP and import it directly into your PostGIS database</p>
  </header>

  <div class="card" id="step-upload">
    <div class="card-label">01 / Select File</div>
    <div class="dropzone" id="dropzone">
      <input type="file" id="fileInput" accept=".zip">
      <div class="drop-icon">◈</div>
      <div class="drop-title">Drag & drop your ZIP file here</div>
      <div class="drop-sub">or click to browse &nbsp;·&nbsp; ZIP must contain .shp .shx .dbf .prj</div>
    </div>
    <div id="file-info" class="hidden" style="margin-top:16px">
      <div class="stats" id="file-stats"></div>
    </div>
  </div>

  <div class="card hidden" id="step-preview">
    <div class="card-label">02 / Data Preview</div>
    <div class="stats" id="preview-stats"></div>
    <div class="preview-wrap">
      <table>
        <thead id="preview-head"></thead>
        <tbody id="preview-body"></tbody>
      </table>
    </div>
    <p style="margin-top:10px;font-size:11px;color:var(--muted)">Showing up to 100 rows</p>
  </div>

  <div class="card hidden" id="step-config">
    <div class="card-label">03 / Import Settings</div>
    <div class="form-row" style="grid-template-columns:1fr 1fr 1fr">
      <div class="form-group">
        <label>Database</label>
        <select id="db-select" onchange="onDbChange()">
          {% for db in databases %}
          <option value="{{ db }}">{{ db }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="form-group">
        <label>Schema</label>
        <select id="schema-select">
          <option value="public">public</option>
        </select>
      </div>
      <div class="form-group">
        <label>Table name</label>
        <input type="text" id="table-name" placeholder="e.g. my_layer">
      </div>
    </div>
    <div class="options-row">
      <div class="form-group">
        <label>SRID</label>
        <input type="text" id="srid-input" value="4326" placeholder="4326">
      </div>
      <div class="form-group">
        <label>Import mode</label>
        <div class="toggle-group">
          <button class="toggle-btn active" data-mode="create"  onclick="setMode('create')">Create</button>
          <button class="toggle-btn"        data-mode="append"  onclick="setMode('append')">Append</button>
          <button class="toggle-btn"        data-mode="replace" onclick="setMode('replace')">Replace</button>
        </div>
      </div>
      <div class="form-group">
        <label>Spatial index</label>
        <div class="toggle-group">
          <button class="toggle-btn active" data-idx="yes" onclick="setIdx(true)">Yes</button>
          <button class="toggle-btn"        data-idx="no"  onclick="setIdx(false)">No</button>
        </div>
      </div>
    </div>
    <div style="display:flex;gap:12px">
      <button class="btn btn-primary" id="import-btn" onclick="doImport()">
        <span>▸ Import into PostGIS</span>
      </button>
      <button class="btn btn-ghost" onclick="reset()">↺ Reset</button>
    </div>
  </div>

  <div class="card hidden" id="step-log">
    <div class="card-label">04 / Import Log</div>
    <div class="log" id="log-output"></div>
  </div>
</div>

<script>
let currentFile = null, importMode = 'create', spatialIdx = true;

const dz = document.getElementById('dropzone');
dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('drag-over'); });
dz.addEventListener('dragleave', ()  => dz.classList.remove('drag-over'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag-over'); const f = e.dataTransfer.files[0]; if (f) handleFile(f); });
document.getElementById('fileInput').addEventListener('change', e => { if (e.target.files[0]) handleFile(e.target.files[0]); });

function setMode(m) { importMode = m; document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === m)); }
function setIdx(v)  { spatialIdx = v; document.querySelectorAll('[data-idx]').forEach(b  => b.classList.toggle('active', (b.dataset.idx === 'yes') === v)); }

async function handleFile(file) {
  currentFile = file;
  show('step-log');
  log('info', `▸ File: ${file.name}  (${(file.size/1024).toFixed(1)} KB)`);
  const fd = new FormData(); fd.append('file', file);
  log('muted', '  Parsing shapefile...');
  try {
    const data = await fetch('/preview', { method:'POST', body:fd }).then(r => r.json());
    if (data.error) { log('err', '✗ ' + data.error); return; }
    document.getElementById('file-stats').innerHTML = `
      <span class="stat">Features: <strong>${data.n_records}</strong></span>
      <span class="stat">Fields: <strong>${data.fields.length}</strong></span>
      <span class="stat">Geometry: <span class="badge badge-green">${data.shape_type}</span></span>
      ${data.srid ? `<span class="stat">CRS: <span class="badge badge-blue">EPSG:${data.srid}</span></span>` : ''}`;
    show('file-info');
    document.getElementById('preview-head').innerHTML = '<tr>' + data.fields.map(f => `<th>${f.name}<br><span style="color:var(--muted);font-size:9px">${f.type}</span></th>`).join('') + '</tr>';
    document.getElementById('preview-body').innerHTML = data.records.map(r => '<tr>' + data.fields.map(f => `<td>${r[f.name]??''}</td>`).join('') + '</tr>').join('');
    document.getElementById('preview-stats').innerHTML = `<span class="stat">Previewing <strong>${data.records.length}</strong> of <strong>${data.n_records}</strong> features</span>`;
    document.getElementById('table-name').value = file.name.replace('.zip','').replace(/[^a-z0-9_]/gi,'_').toLowerCase();
    if (data.srid) document.getElementById('srid-input').value = data.srid;
    show('step-preview'); show('step-config');
    log('ok', `✓ Shapefile parsed — ${data.n_records} features, ${data.fields.length} fields`);
  } catch(e) { log('err', '✗ ' + e.message); }
}

async function onDbChange() {
  const db = document.getElementById('db-select').value;
  try {
    const data = await fetch('/databases').then(r => r.json());
    const schemas = data[db] || ['public'];
    const sel = document.getElementById('schema-select');
    sel.innerHTML = schemas.map(s =>
      '<option value="' + s + '"' + (s==='public'?' selected':'') + '>' + s + '</option>'
    ).join('');
  } catch(e) { /* keep current options */ }
}

async function doImport() {
  const table  = document.getElementById('table-name').value.trim();
  const schema = document.getElementById('schema-select').value;
  const srid   = document.getElementById('srid-input').value.trim() || '4326';
  if (!table)       { log('err', '✗ Please enter a table name'); return; }
  if (!currentFile) { log('err', '✗ No file loaded'); return; }
  const btn = document.getElementById('import-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span><span>Importing...</span>';
  log('info', `▸ Importing → ${schema}.${table}  (SRID: ${srid}, mode: ${importMode})`);
  const fd = new FormData();
  const db = document.getElementById('db-select').value;
  fd.append('file', currentFile); fd.append('table', table); fd.append('schema', schema);
  fd.append('db', db); fd.append('srid', srid); fd.append('mode', importMode); fd.append('spatial_index', spatialIdx ? '1' : '0');
  try {
    const data = await fetch('/import', { method:'POST', body:fd }).then(r => r.json());
    if (data.error) { log('err', '✗ ' + data.error); }
    else {
      log('ok',    '✓ Import completed successfully!');
      log('ok',    `  Table: ${schema}.${table}`);
      log('ok',    `  Features imported: ${data.imported}`);
      if (spatialIdx) log('ok', '  Spatial index (GIST) created');
      log('muted', `  Time: ${data.elapsed}s`);
    }
  } catch(e) { log('err', '✗ Network error: ' + e.message); }
  btn.disabled = false; btn.innerHTML = '<span>▸ Import into PostGIS</span>';
}

function log(type, msg) {
  const el = document.getElementById('log-output');
  const ln = document.createElement('span'); ln.className = 'log-line ' + type; ln.textContent = msg;
  el.appendChild(ln); el.scrollTop = el.scrollHeight;
}
function show(id) { document.getElementById(id).classList.remove('hidden'); }
function reset() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('log-output').innerHTML = '';
  ['file-info','step-preview','step-config','step-log'].forEach(id => document.getElementById(id).classList.add('hidden'));
}

// Load schemas for the initially selected database on page load
onDbChange();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(HTML, databases=list(DATABASES.keys()))

@app.route('/databases', methods=['GET'])
def list_databases():
    """Return available DB labels and their schemas."""
    result = {}
    for label in DATABASES.keys():
        result[label] = get_schemas(label)
    return jsonify(result)


@app.route('/preview', methods=['POST'])
def preview():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file received'})
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'upload.zip')
            f.save(zip_path)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(tmpdir)

            shp_file = find_file(tmpdir, '.shp')
            if not shp_file:
                return jsonify({'error': 'No .shp file found inside the ZIP'})

            prj_file = find_file(os.path.dirname(shp_file), '.prj')
            srid     = detect_srid(prj_file)

            sf      = shapefile.Reader(shp_file)
            fields  = [{'name': f[0], 'type': f[1]} for f in sf.fields[1:]]  # skip DeletionFlag
            records = []
            for sr in sf.iterShapeRecords():
                row = {fields[i]['name']: str(sr.record[i]) for i in range(len(fields))}
                records.append(row)
                if len(records) >= 100:
                    break

            return jsonify({
                'fields':     fields,
                'records':    records,
                'n_records':  len(sf),
                'shape_type': SHAPE_TYPES.get(sf.shapeType, f'Type {sf.shapeType}'),
                'srid':       srid,
            })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/import', methods=['POST'])
def import_shp():
    import time
    f             = request.files.get('file')
    table         = request.form.get('table', '').strip()
    schema        = request.form.get('schema', 'public').strip()
    srid          = request.form.get('srid', '4326').strip()
    mode          = request.form.get('mode', 'create')
    spatial_index = request.form.get('spatial_index', '1') == '1'

    db_label      = request.form.get('db', None)

    if not f or not table:
        return jsonify({'error': 'Missing parameters'})

    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, 'upload.zip')
            f.save(zip_path)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(tmpdir)

            shp_file = find_file(tmpdir, '.shp')
            if not shp_file:
                return jsonify({'error': 'No .shp file found inside the ZIP'})

            sf     = shapefile.Reader(shp_file)
            fields = sf.fields[1:]  # skip DeletionFlag: [name, type, size, decimal]

            conn = get_conn(db_label)
            cur  = conn.cursor()

            full_table = sql.Identifier(schema, table)

            # --- Handle import mode ---
            if mode == 'replace':
                cur.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(full_table))

            if mode in ('create', 'replace'):
                col_defs = ', '.join(
                    f'"{f[0]}" {pg_type(f[1])}' for f in fields
                )
                cur.execute(sql.SQL(
                    'CREATE TABLE {} (gid SERIAL PRIMARY KEY, {} , geom GEOMETRY)'
                ).format(full_table, sql.SQL(col_defs)))

            # --- Insert rows ---
            col_names = ', '.join(f'"{f[0]}"' for f in fields)
            placeholders = ', '.join(['%s'] * len(fields))
            insert_sql = sql.SQL(
                'INSERT INTO {} ({}, geom) VALUES ({}, ST_GeomFromText(%s, {}))'
            ).format(
                full_table,
                sql.SQL(col_names),
                sql.SQL(placeholders),
                sql.Literal(int(srid))
            )

            count = 0
            for sr in sf.iterShapeRecords():
                wkt = geom_to_wkt(sr.shape)
                if wkt is None:
                    continue
                values = [str(v) if v is not None else None for v in sr.record]
                cur.execute(insert_sql, values + [wkt])
                count += 1

            # --- Spatial index ---
            if spatial_index:
                cur.execute(sql.SQL(
                    'CREATE INDEX ON {} USING GIST (geom)'
                ).format(full_table))

            conn.commit()
            conn.close()

            return jsonify({'imported': count, 'elapsed': round(time.time() - t0, 2)})

    except Exception as e:
        return jsonify({'error': str(e)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)