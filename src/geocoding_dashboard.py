"""Address Geocoding Dashboard (Dash) - fixed for the merged repo.

Run with:  uv run poe geocoding-dashboard   (DB_DATABASE defaults to 'housing')

What changed vs. the version migrated from the -copy repo, and why:

1. Reads/writes target `unique_addresses` directly. The restored schema has
   no `geocoded_addresses` table - geocoding results live on
   `unique_addresses` itself - so every query here uses that table. The
   compatibility view from the merge playbook (Section 5) is no longer
   needed; if you created it, you can `DROP VIEW IF EXISTS geocoded_addresses;`.

2. SQLAlchemy 2.0 compatibility. `get_geocoding_stats()` used `dict(row)`,
   which SQLAlchemy 2.0 removed (Rows are tuple-like); it now uses
   `row._mapping`. The engine URL is built with `URL.create`, so passwords
   with special characters cannot break the connection string.

3. `update_corrections` returned `conn.execute(df.to_dict("records"))` -
   a DataFrame dict is not executable, so this callback crashed on every
   30-second interval tick regardless of database state. It now simply
   returns the records.

4. The manual-correction write path delegates to
   `GeocodingService.manually_update_address`, which already performs the
   same UPDATE + PostGIS geom refresh + `address_correction_log` entries
   against `unique_addresses` inside one transaction. The dashboard's own
   psycopg2/RealDictCursor copy of that logic is gone (one less duplicate,
   and no direct psycopg2 dependency here).

5. Plotly map API shim. Plotly >= 5.24 replaces the Mapbox traces with
   MapLibre ones (`px.scatter_map` / `go.Scattermap`, layout key `map`);
   the old names are deprecated in Plotly 6. The dashboard picks whichever
   the installed Plotly provides, and the empty-map branch no longer mixes
   a MapLibre trace with `mapbox` layout keys (which rendered blank).

6. Defensive formatting: no division by zero when the table is empty, and
   a NULL average confidence renders as an em dash instead of raising.
"""

from typing import Optional

import dash
from dash import dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import URL, create_engine, text

from scripts.config import get_db_config
from geocoding_service import GeocodingService

# --------------------------------------------------------------------------
# App / database setup
# --------------------------------------------------------------------------

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = "Address Geocoding Dashboard"

db_config = get_db_config()
engine = create_engine(
    URL.create(
        drivername="postgresql",  # resolves to psycopg2 (installed for this app)
        username=db_config["username"],
        password=db_config["password"],
        host=db_config["hostname"],
        port=int(db_config["port"]),
        database=db_config["database"],
    )
)

geocoding_service = GeocodingService()

NASHVILLE = {"lat": 36.1627, "lon": -86.7816}

# Plotly >= 5.24 ships MapLibre-based maps; older versions only have Mapbox.
_MAPLIBRE = hasattr(px, "scatter_map")

# --------------------------------------------------------------------------
# Data access - all against unique_addresses in the restored 'housing' DB
# --------------------------------------------------------------------------


def get_geocoded_addresses(limit: int = 1000, status: Optional[str] = None) -> pd.DataFrame:
    """Addresses with their geocoding state, newest results first."""
    query = """
    SELECT
        ua.address_id  AS id,
        ua.address     AS original_address,
        ua.corrected_address,
        ua.latitude,
        ua.longitude,
        ua.confidence,
        ua.source,
        ua.status
    FROM unique_addresses ua
    """
    params = {"limit": limit}
    if status:
        query += " WHERE ua.status = :status"
        params["status"] = status
    query += " ORDER BY ua.geocoded_at DESC NULLS LAST LIMIT :limit"

    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def get_geocoding_stats() -> dict:
    """Aggregate geocoding statistics."""
    query = """
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE latitude IS NOT NULL AND longitude IS NOT NULL) AS geocoded,
        COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
        COUNT(*) FILTER (WHERE status = 'MANUALLY_CORRECTED') AS manually_corrected,
        COUNT(*) FILTER (WHERE source = 'OSM') AS osm_source,
        COUNT(*) FILTER (WHERE source = 'HERE') AS here_source,
        AVG(confidence) FILTER (WHERE confidence IS NOT NULL) AS avg_confidence
    FROM unique_addresses
    """
    with engine.connect() as conn:
        row = conn.execute(text(query)).fetchone()
    # SQLAlchemy 2.0: Rows are tuple-like; dict(row) was removed - use _mapping.
    return dict(row._mapping)


def get_correction_logs(limit: int = 100) -> pd.DataFrame:
    """Recent manual-correction log entries with the address they touched."""
    query = """
    SELECT
        l.id,
        l.changed_at,
        l.changed_by,
        l.field_changed,
        l.original_value,
        l.new_value,
        l.reason,
        ua.address AS original_address
    FROM address_correction_log l
    JOIN unique_addresses ua ON l.address_id = ua.address_id
    ORDER BY l.changed_at DESC
    LIMIT :limit
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params={"limit": limit})


def save_manual_correction(address_id: int, corrected_address: str,
                           latitude: Optional[float], longitude: Optional[float],
                           user: str, reason: str) -> bool:
    """Persist a manual fix via the service (UPDATE + geom + correction log)."""
    return geocoding_service.manually_update_address(
        address_id=address_id,
        corrected_address=corrected_address,
        latitude=latitude,
        longitude=longitude,
        changed_by=user,
        reason=reason,
    )


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


def _count_with_pct(part, total) -> str:
    part = int(part or 0)
    total = int(total or 0)
    if total == 0:
        return f"{part} (0.0%)"
    return f"{part} ({part / total * 100:.1f}%)"


def _avg_confidence_label(avg) -> str:
    if avg is None:
        return "\u2014"  # em dash: no confidence values yet
    return f"{float(avg) * 100:.1f}%"


def _map_figure(map_df: Optional[pd.DataFrame]) -> go.Figure:
    """Build the address map with whichever map API this Plotly provides."""
    common = dict(
        lat="latitude",
        lon="longitude",
        hover_name="original_address",
        hover_data=["corrected_address", "confidence", "source"],
        color="status",
        color_discrete_map={
            "GEOCODED": "green",
            "FAILED": "red",
            "MANUALLY_CORRECTED": "blue",
        },
        zoom=10,
        height=600,
    )
    if map_df is not None and len(map_df) > 0:
        if _MAPLIBRE:
            fig = px.scatter_map(map_df, **common)
            fig.update_layout(map_style="open-street-map")
        else:
            fig = px.scatter_mapbox(map_df, **common)
            fig.update_layout(mapbox_style="open-street-map")
    else:
        if _MAPLIBRE:
            fig = go.Figure(go.Scattermap())
            fig.update_layout(
                map_style="open-street-map",
                map=dict(center=NASHVILLE, zoom=10),
                height=600,
            )
        else:
            fig = go.Figure(go.Scattermapbox())
            fig.update_layout(
                mapbox_style="open-street-map",
                mapbox=dict(center=NASHVILLE, zoom=10),
                height=600,
            )
    fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
    return fig


# --------------------------------------------------------------------------
# Layout (unchanged from the migrated version)
# --------------------------------------------------------------------------

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col([
            html.H1("Address Geocoding Dashboard", className="text-center my-4"),
            html.Hr()
        ])
    ]),

    dbc.Row([
        dbc.Col([
            html.H4("Geocoding Statistics"),
            html.Div(id="stats-container")
        ], width=12)
    ]),

    dbc.Row([
        dbc.Col([
            html.H4("Address Map"),
            dcc.Graph(id="address-map", style={"height": "600px"})
        ], width=8),

        dbc.Col([
            html.H4("Status Filter"),
            dcc.Dropdown(
                id="status-filter",
                options=[
                    {"label": "All", "value": "ALL"},
                    {"label": "Geocoded", "value": "GEOCODED"},
                    {"label": "Failed", "value": "FAILED"},
                    {"label": "Manually Corrected", "value": "MANUALLY_CORRECTED"}
                ],
                value="ALL"
            ),
            html.H4("Single Address Geocoding", className="mt-4"),
            dcc.Input(
                id="geocode-input",
                type="text",
                placeholder="Enter address to geocode",
                className="form-control"
            ),
            html.Button(
                "Geocode",
                id="geocode-button",
                className="btn btn-primary mt-2"
            ),
            html.Div(id="geocode-result", className="mt-2")
        ], width=4)
    ]),

    dbc.Row([
        dbc.Col([
            html.H4("Address Table"),
            dash_table.DataTable(
                id="address-table",
                columns=[
                    {"name": "ID", "id": "id"},
                    {"name": "Original Address", "id": "original_address"},
                    {"name": "Corrected Address", "id": "corrected_address"},
                    {"name": "Latitude", "id": "latitude"},
                    {"name": "Longitude", "id": "longitude"},
                    {"name": "Confidence", "id": "confidence"},
                    {"name": "Source", "id": "source"},
                    {"name": "Status", "id": "status"}
                ],
                page_size=10,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left"},
                style_header={"fontWeight": "bold"},
                row_selectable="single"
            )
        ], width=12)
    ]),

    dbc.Modal([
        dbc.ModalHeader("Edit Address"),
        dbc.ModalBody([
            dbc.Form([
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Original Address"),
                        dbc.Input(id="modal-original", disabled=True)
                    ], width=12)
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Corrected Address"),
                        dbc.Input(id="modal-corrected")
                    ], width=12)
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Latitude"),
                        dbc.Input(id="modal-latitude", type="number")
                    ], width=6),
                    dbc.Col([
                        dbc.Label("Longitude"),
                        dbc.Input(id="modal-longitude", type="number")
                    ], width=6)
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Reason for Correction"),
                        dbc.Textarea(id="modal-reason", placeholder="Explain why this correction is needed")
                    ], width=12)
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Your Name"),
                        dbc.Input(id="modal-user", placeholder="Enter your name")
                    ], width=12)
                ])
            ])
        ]),
        dbc.ModalFooter([
            dbc.Button("Save", id="modal-save", className="ms-auto"),
            dbc.Button("Cancel", id="modal-cancel", className="ms-2")
        ])
    ], id="edit-modal", is_open=False),

    dbc.Row([
        dbc.Col([
            html.H4("Recent Corrections"),
            dash_table.DataTable(
                id="corrections-table",
                columns=[
                    {"name": "Date", "id": "changed_at"},
                    {"name": "User", "id": "changed_by"},
                    {"name": "Address", "id": "original_address"},
                    {"name": "Field", "id": "field_changed"},
                    {"name": "From", "id": "original_value"},
                    {"name": "To", "id": "new_value"},
                    {"name": "Reason", "id": "reason"}
                ],
                page_size=5,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left"},
                style_header={"fontWeight": "bold"}
            )
        ], width=12)
    ]),

    dcc.Interval(
        id="interval-component",
        interval=30 * 1000,  # refresh every 30 seconds
        n_intervals=0
    ),

    dcc.Store(id="selected-address-id")
], fluid=True)


# --------------------------------------------------------------------------
# Callbacks
# --------------------------------------------------------------------------

@app.callback(
    Output("stats-container", "children"),
    Input("interval-component", "n_intervals")
)
def update_stats(n):
    stats = get_geocoding_stats()

    def card(title, value, extra_class=""):
        return dbc.Col([
            dbc.Card([
                dbc.CardBody([
                    html.H5(title, className="card-title"),
                    html.H3(value, className=f"card-text {extra_class}".strip())
                ])
            ])
        ])

    return dbc.Row([
        card("Total Addresses", int(stats["total"] or 0)),
        card("Geocoded", _count_with_pct(stats["geocoded"], stats["total"]), "text-success"),
        card("Failed", _count_with_pct(stats["failed"], stats["total"]), "text-danger"),
        card("Manually Fixed", _count_with_pct(stats["manually_corrected"], stats["total"]), "text-primary"),
        card("Avg Confidence", _avg_confidence_label(stats["avg_confidence"])),
    ])


@app.callback(
    [Output("address-table", "data"),
     Output("address-map", "figure")],
    [Input("status-filter", "value"),
     Input("interval-component", "n_intervals")]
)
def update_addresses(status, n):
    status_filter = None if status == "ALL" else status
    df = get_geocoded_addresses(status=status_filter)

    if len(df) > 0 and (~df["latitude"].isna()).any():
        fig = _map_figure(df[~df["latitude"].isna()])
    else:
        fig = _map_figure(None)

    return df.to_dict("records"), fig


@app.callback(
    Output("corrections-table", "data"),
    Input("interval-component", "n_intervals")
)
def update_corrections(n):
    # Previous version wrapped this in conn.execute(...), which is not an
    # executable object and crashed on every interval tick.
    return get_correction_logs().to_dict("records")


@app.callback(
    [Output("edit-modal", "is_open"),
     Output("modal-original", "value"),
     Output("modal-corrected", "value"),
     Output("modal-latitude", "value"),
     Output("modal-longitude", "value"),
     Output("selected-address-id", "data")],
    [Input("address-table", "selected_rows"),
     Input("modal-save", "n_clicks"),
     Input("modal-cancel", "n_clicks")],
    [State("address-table", "data"),
     State("edit-modal", "is_open"),
     State("selected-address-id", "data")]
)
def toggle_modal(selected_rows, save_clicks, cancel_clicks, data, is_open, address_id):
    ctx = dash.callback_context

    if not ctx.triggered:
        return is_open, "", "", None, None, address_id

    button_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if button_id == "address-table" and selected_rows:
        row = data[selected_rows[0]]
        return True, row["original_address"], row["corrected_address"], row["latitude"], row["longitude"], row["id"]

    if button_id in ["modal-save", "modal-cancel"]:
        return False, "", "", None, None, address_id

    return is_open, "", "", None, None, address_id


@app.callback(
    Output("geocode-result", "children"),
    Input("geocode-button", "n_clicks"),
    State("geocode-input", "value")
)
def geocode_address(n_clicks, address):
    if not n_clicks or not address:
        return ""

    result = geocoding_service.geocode_address(address)

    if result.get("latitude") and result.get("longitude"):
        # Only persist if the service exposes a storage hook for ad-hoc
        # strings; results for known addresses are already stored by the
        # service itself.
        store = getattr(geocoding_service, "store_geocoding_result", None)
        if callable(store):
            try:
                store(address, result)
            except Exception:
                pass  # display the result even if persisting it fails

        return html.Div([
            html.P(f"Geocoded to: {result.get('match', address)}", className="text-success"),
            html.P(f"Coordinates: ({result.get('latitude')}, {result.get('longitude')})"),
            html.P(f"Confidence: {result.get('confidence', 0) * 100:.1f}%"),
            html.P(f"Source: {result.get('source', 'Unknown')}")
        ])
    return html.P(f"Geocoding failed: {result.get('error', 'Unknown error')}", className="text-danger")


@app.callback(
    Output("interval-component", "n_intervals"),
    Input("modal-save", "n_clicks"),
    [State("selected-address-id", "data"),
     State("modal-corrected", "value"),
     State("modal-latitude", "value"),
     State("modal-longitude", "value"),
     State("modal-user", "value"),
     State("modal-reason", "value"),
     State("interval-component", "n_intervals")]
)
def save_correction(n_clicks, address_id, corrected, latitude, longitude, user, reason, n_intervals):
    if not n_clicks or not address_id:
        return n_intervals
    
    print(f"Saving correction for address_id={address_id}: '{corrected}' ({latitude}, {longitude})")

    save_manual_correction(
        address_id=address_id,
        corrected_address=corrected,
        latitude=latitude,
        longitude=longitude,
        user=user or "Anonymous",
        reason=reason or "No reason provided",
    )

    # Force refresh by incrementing the interval counter
    return n_intervals + 1


# Run the app
if __name__ == "__main__":
    import os

    # Debug UI (in-browser tracebacks) is opt-in: set DASH_DEBUG=1.
    # The auto-reloader stays OFF either way: file churn in the project tree
    # (logs, __pycache__, editors/sync tools) makes it restart-loop the
    # server, which the browser reports as "Server Unavailable".
    # localhost binding keeps the Werkzeug debugger off the LAN.
    debug = os.getenv("DASH_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(debug=debug, use_reloader=False, host="127.0.0.1", port=8050)