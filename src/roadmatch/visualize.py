from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from roadmatch.graph import RoadGraph
from roadmatch.models import CandidatePath, DetectionSet


def candidate_features(
    candidates: Iterable[CandidatePath],
    report: Dict[str, Any],
) -> List[Dict[str, Any]]:
    report_by_id = {item["path_id"]: item for item in report.get("candidates", [])}
    features = []
    for candidate in candidates:
        metrics = report_by_id.get(candidate.path_id, {})
        properties = {
            "path_id": candidate.path_id,
            "rank": metrics.get("rank"),
            "confidence": metrics.get("confidence"),
            "score": metrics.get("score"),
            "length_m": candidate.length_m,
            "expected_observed_length_m": metrics.get("expected_observed_length_m"),
            "length_delta_m": metrics.get("length_delta_m"),
            "node_count": len(candidate.nodes),
            "candidate_event_count": len(candidate.events),
            "matched_event_count": metrics.get("matched_event_count"),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lon, lat in candidate.geometry],
                },
                "properties": properties,
            }
        )
    return features


def write_candidates_geojson(
    candidates: Iterable[CandidatePath],
    report: Dict[str, Any],
    path: Path,
) -> None:
    payload = {"type": "FeatureCollection", "features": candidate_features(candidates, report)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_map_html(
    graph: RoadGraph,
    candidates: List[CandidatePath],
    report: Dict[str, Any],
    detection: DetectionSet,
    path: Path,
) -> None:
    features = candidate_features(candidates, report)
    event_features = _event_features(graph, report)
    center = _map_center(features, detection)
    payload = {
        "candidates": {"type": "FeatureCollection", "features": features},
        "events": {"type": "FeatureCollection", "features": event_features},
        "start": detection.start.to_dict(),
        "end": detection.end.to_dict(),
        "summary": {
            "observed_length_m": detection.observed_length_m,
            "observed_event_count": len(detection.events),
            "top_k": len(report.get("candidates", [])),
        },
    }

    title = "Roadmatch"
    top = report.get("candidates", [{}])[0] if report.get("candidates") else {}
    subtitle = (
        f"Top path {html.escape(str(top.get('path_id', 'n/a')))} · "
        f"confidence {float(top.get('confidence', 0.0)):.2%} · "
        f"length {float(top.get('length_m', 0.0)):.0f} m"
    )
    script_payload = json.dumps(payload, ensure_ascii=False)
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body, #map {{
      height: 100%;
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #map {{
      background: #eef2f7;
    }}
    .panel {{
      position: absolute;
      top: 14px;
      left: 14px;
      z-index: 500;
      width: min(360px, calc(100vw - 28px));
      padding: 12px 14px;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid #d4d8de;
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
      color: #1f2937;
    }}
    .panel h1 {{
      margin: 0 0 6px;
      font-size: 17px;
      font-weight: 650;
    }}
    .panel p {{
      margin: 0;
      font-size: 13px;
      line-height: 1.45;
    }}
    .legend {{
      margin-top: 9px;
      display: grid;
      grid-template-columns: 14px 1fr;
      gap: 6px 8px;
      font-size: 12px;
      align-items: center;
    }}
    .swatch {{
      width: 14px;
      height: 4px;
      border-radius: 2px;
      background: #2563eb;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <h1>Roadmatch</h1>
    <p>{subtitle}</p>
    <div class="legend">
      <span class="swatch" style="background:#2563eb"></span><span>Top candidate</span>
      <span class="swatch" style="background:#f97316"></span><span>Other candidates</span>
      <span class="swatch" style="background:#7c3aed"></span><span>Matched detections</span>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const data = {script_payload};
    const colors = ['#2563eb', '#f97316', '#16a34a', '#dc2626', '#0891b2', '#9333ea'];

    if (window.L) {{
      renderLeaflet();
    }} else {{
      renderStaticSvg();
    }}

    function renderLeaflet() {{
      const map = L.map('map').setView([{center[1]:.7f}, {center[0]:.7f}], 12);
      L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors'
      }}).addTo(map);

      const layer = L.geoJSON(data.candidates, {{
        style: feature => {{
          const rank = feature.properties.rank || 1;
          return {{
            color: colors[Math.min(rank - 1, colors.length - 1)],
            weight: rank === 1 ? 7 : 4,
            opacity: rank === 1 ? 0.92 : 0.50
          }};
        }},
        onEachFeature: (feature, l) => {{
          const p = feature.properties;
          l.bindPopup(
            `<b>${{p.path_id}}</b><br>` +
            `rank: ${{p.rank}}<br>` +
            `confidence: ${{(p.confidence * 100).toFixed(1)}}%<br>` +
            `score: ${{p.score.toFixed(3)}}<br>` +
            `road length: ${{p.length_m.toFixed(0)}} m`
          );
        }}
      }}).addTo(map);

      L.geoJSON(data.events, {{
        pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{
          radius: 6,
          fillColor: '#7c3aed',
          color: '#ffffff',
          weight: 2,
          fillOpacity: 0.9
        }}),
        onEachFeature: (feature, l) => {{
          const p = feature.properties;
          l.bindPopup(`event ${{p.observed_index}}<br>${{p.movement}}<br>score: ${{p.score.toFixed(2)}}`);
        }}
      }}).addTo(map);

      L.marker([data.start.lat, data.start.lon]).addTo(map).bindPopup(`Start: ${{data.start.name}}`);
      L.marker([data.end.lat, data.end.lon]).addTo(map).bindPopup(`End: ${{data.end.name}}`);
      if (layer.getBounds().isValid()) {{
        map.fitBounds(layer.getBounds(), {{padding: [28, 28]}});
      }}
    }}

    function renderStaticSvg() {{
      const host = document.getElementById('map');
      const width = Math.max(host.clientWidth, 320);
      const height = Math.max(host.clientHeight, 320);
      const all = [];
      for (const feature of data.candidates.features) {{
        for (const coord of feature.geometry.coordinates) all.push(coord);
      }}
      all.push([data.start.lon, data.start.lat], [data.end.lon, data.end.lat]);
      if (!all.length) return;

      const minLon = Math.min(...all.map(c => c[0]));
      const maxLon = Math.max(...all.map(c => c[0]));
      const minLat = Math.min(...all.map(c => c[1]));
      const maxLat = Math.max(...all.map(c => c[1]));
      const pad = 56;
      const spanLon = Math.max(maxLon - minLon, 0.000001);
      const spanLat = Math.max(maxLat - minLat, 0.000001);
      const sx = (width - pad * 2) / spanLon;
      const sy = (height - pad * 2) / spanLat;
      const scale = Math.min(sx, sy);
      const usedW = spanLon * scale;
      const usedH = spanLat * scale;
      const ox = (width - usedW) / 2;
      const oy = (height - usedH) / 2;

      const project = coord => [
        ox + (coord[0] - minLon) * scale,
        oy + usedH - (coord[1] - minLat) * scale
      ];
      const points = coords => coords.map(c => project(c).join(',')).join(' ');
      const marker = (coord, fill, label) => {{
        const [x, y] = project(coord);
        return `<g><circle cx="${{x}}" cy="${{y}}" r="7" fill="${{fill}}" stroke="#fff" stroke-width="2"/>` +
          `<text x="${{x + 10}}" y="${{y - 10}}" font-size="12" font-weight="650" fill="#111827">${{label}}</text></g>`;
      }};

      const lines = data.candidates.features.map((feature, index) => {{
        const rank = feature.properties.rank || index + 1;
        const color = colors[Math.min(rank - 1, colors.length - 1)];
        const width = rank === 1 ? 7 : 4;
        const opacity = rank === 1 ? 0.95 : 0.55;
        return `<polyline points="${{points(feature.geometry.coordinates)}}" fill="none" ` +
          `stroke="${{color}}" stroke-width="${{width}}" stroke-linecap="round" ` +
          `stroke-linejoin="round" opacity="${{opacity}}"><title>${{feature.properties.path_id}}</title></polyline>`;
      }}).join('');

      const eventDots = data.events.features.map(feature => {{
        const [x, y] = project(feature.geometry.coordinates);
        return `<circle cx="${{x}}" cy="${{y}}" r="4" fill="#7c3aed" stroke="#fff" stroke-width="1.5" opacity="0.92"/>`;
      }}).join('');

      host.innerHTML = `<svg width="${{width}}" height="${{height}}" viewBox="0 0 ${{width}} ${{height}}" ` +
        `xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Roadmatch route candidates">` +
        `<rect width="100%" height="100%" fill="#eef2f7"/>` +
        `<g opacity="0.22" stroke="#94a3b8" stroke-width="1">` +
        `<line x1="${{pad}}" y1="${{height - pad}}" x2="${{width - pad}}" y2="${{height - pad}}"/>` +
        `<line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{height - pad}}"/>` +
        `</g>${{lines}}${{eventDots}}` +
        `${{marker([data.start.lon, data.start.lat], '#0f766e', 'Start')}}` +
        `${{marker([data.end.lon, data.end.lat], '#be123c', 'End')}}` +
        `<text x="${{width - pad}}" y="${{height - 24}}" text-anchor="end" font-size="12" fill="#475569">` +
        `Offline SVG fallback · route geometry shown without map tiles</text>` +
        `</svg>`;
    }}
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def _event_features(graph: RoadGraph, report: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = report.get("candidates", [])
    if not candidates:
        return []
    features = []
    for alignment in candidates[0].get("event_alignment", []):
        candidate = alignment.get("candidate")
        if not candidate:
            continue
        node = graph.nodes.get(str(candidate.get("node_id")))
        if node is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [node.lon, node.lat]},
                "properties": {
                    "observed_index": alignment.get("observed_index"),
                    "movement": candidate.get("movement"),
                    "score": alignment.get("score", 0.0),
                    "position_score": alignment.get("position_score", 0.0),
                },
            }
        )
    return features


def _map_center(features: List[Dict[str, Any]], detection: DetectionSet) -> tuple[float, float]:
    coords: List[List[float]] = []
    for feature in features:
        coords.extend(feature.get("geometry", {}).get("coordinates", []))
    if not coords:
        return detection.start.lon, detection.start.lat
    lon = sum(coord[0] for coord in coords) / len(coords)
    lat = sum(coord[1] for coord in coords) / len(coords)
    return lon, lat
