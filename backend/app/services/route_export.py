"""Route export to GPX and KML formats (Story 4.4)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.models.route import Route


def export_gpx(route: Route, include_descriptions: bool = True) -> str:
    """Export a route to GPX 1.1 format.

    Generates a GPX file with waypoints for each stop and a track
    connecting all points in order.
    """
    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": "Terra Incognita",
        "xmlns": "http://www.topografix.com/GPX/1/1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": (
            "http://www.topografix.com/GPX/1/1 "
            "http://www.topografix.com/GPX/1/1/gpx.xsd"
        ),
    })

    # Metadata
    metadata = ET.SubElement(gpx, "metadata")
    name_el = ET.SubElement(metadata, "name")
    name_el.text = f"Terra Incognita Route ({route.places_count} places)"
    time_el = ET.SubElement(metadata, "time")
    time_el.text = route.created_at.isoformat()

    desc_el = ET.SubElement(metadata, "desc")
    mode_label = route.transport_mode.value
    dist_km = route.total_distance_m / 1000
    dur_h = route.total_duration_s / 3600
    desc_el.text = (
        f"Mode: {mode_label}, Distance: {dist_km:.1f} km, "
        f"Duration: {dur_h:.1f}h, Stops: {route.places_count}"
    )

    # Waypoints
    for wp in route.waypoints:
        wpt = ET.SubElement(gpx, "wpt", {
            "lat": str(wp.place.coordinates.lat),
            "lon": str(wp.place.coordinates.lng),
        })
        wpt_name = ET.SubElement(wpt, "name")
        wpt_name.text = wp.place.name or f"Point {wp.order}"

        if include_descriptions and wp.place.description:
            wpt_desc = ET.SubElement(wpt, "desc")
            wpt_desc.text = wp.place.description

        if wp.place.categories:
            wpt_type = ET.SubElement(wpt, "type")
            wpt_type.text = ", ".join(c.value for c in wp.place.categories)

    # Track
    trk = ET.SubElement(gpx, "trk")
    trk_name = ET.SubElement(trk, "name")
    trk_name.text = f"Route ({route.transport_mode.value})"
    trkseg = ET.SubElement(trk, "trkseg")

    for wp in route.waypoints:
        ET.SubElement(trkseg, "trkpt", {
            "lat": str(wp.place.coordinates.lat),
            "lon": str(wp.place.coordinates.lng),
        })

    return _xml_to_string(gpx)


def export_kml(route: Route, include_descriptions: bool = True) -> str:
    """Export a route to KML format for Google Earth / Maps.

    Creates a KML document with placemarks for each stop and a
    LineString for the route path.
    """
    kml = ET.Element("kml", {"xmlns": "http://www.opengis.net/kml/2.2"})
    doc = ET.SubElement(kml, "Document")

    doc_name = ET.SubElement(doc, "name")
    doc_name.text = f"Terra Incognita Route ({route.places_count} places)"

    doc_desc = ET.SubElement(doc, "description")
    mode_label = route.transport_mode.value
    dist_km = route.total_distance_m / 1000
    dur_h = route.total_duration_s / 3600
    doc_desc.text = (
        f"Mode: {mode_label}, Distance: {dist_km:.1f} km, "
        f"Duration: {dur_h:.1f}h, Stops: {route.places_count}"
    )

    # Styles
    _add_kml_styles(doc)

    # Placemarks for each waypoint
    for wp in route.waypoints:
        pm = ET.SubElement(doc, "Placemark")
        pm_name = ET.SubElement(pm, "name")
        pm_name.text = wp.place.name or f"Point {wp.order}"

        if wp.is_origin:
            style_url = ET.SubElement(pm, "styleUrl")
            style_url.text = "#startStyle"
        elif wp.is_destination:
            style_url = ET.SubElement(pm, "styleUrl")
            style_url.text = "#endStyle"
        else:
            style_url = ET.SubElement(pm, "styleUrl")
            style_url.text = "#waypointStyle"

        if include_descriptions and wp.place.description:
            pm_desc = ET.SubElement(pm, "description")
            pm_desc.text = wp.place.description

        point = ET.SubElement(pm, "Point")
        coords = ET.SubElement(point, "coordinates")
        coords.text = f"{wp.place.coordinates.lng},{wp.place.coordinates.lat},0"

    # Route line
    route_pm = ET.SubElement(doc, "Placemark")
    route_name = ET.SubElement(route_pm, "name")
    route_name.text = f"Route ({mode_label})"
    style_url = ET.SubElement(route_pm, "styleUrl")
    style_url.text = "#routeStyle"

    linestring = ET.SubElement(route_pm, "LineString")
    ET.SubElement(linestring, "tessellate").text = "1"
    coord_el = ET.SubElement(linestring, "coordinates")
    coord_parts = []
    for wp in route.waypoints:
        coord_parts.append(
            f"{wp.place.coordinates.lng},{wp.place.coordinates.lat},0"
        )
    coord_el.text = " ".join(coord_parts)

    return _xml_to_string(kml)


def _add_kml_styles(doc: ET.Element) -> None:
    """Add KML styles for start, end, waypoint, and route."""
    # Route line style
    style = ET.SubElement(doc, "Style", {"id": "routeStyle"})
    line_style = ET.SubElement(style, "LineStyle")
    ET.SubElement(line_style, "color").text = "ff0078ff"  # Orange-ish
    ET.SubElement(line_style, "width").text = "4"

    # Start pin
    style = ET.SubElement(doc, "Style", {"id": "startStyle"})
    icon_style = ET.SubElement(style, "IconStyle")
    ET.SubElement(icon_style, "color").text = "ff00ff00"  # Green
    ET.SubElement(icon_style, "scale").text = "1.2"

    # End pin
    style = ET.SubElement(doc, "Style", {"id": "endStyle"})
    icon_style = ET.SubElement(style, "IconStyle")
    ET.SubElement(icon_style, "color").text = "ff0000ff"  # Red
    ET.SubElement(icon_style, "scale").text = "1.2"

    # Waypoint pin
    style = ET.SubElement(doc, "Style", {"id": "waypointStyle"})
    icon_style = ET.SubElement(style, "IconStyle")
    ET.SubElement(icon_style, "color").text = "ffff7800"  # Blue-ish
    ET.SubElement(icon_style, "scale").text = "1.0"


def _xml_to_string(root: ET.Element) -> str:
    """Convert an XML element tree to a pretty string."""
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )
