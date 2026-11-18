"""Synthetic PDF builder for tests. Writes small, valid or deliberately malformed PDFs; no external files are used."""
import zlib


def text_stream(lines, size=12):
    ops = ["BT", f"/F1 {size} Tf", "14 TL", "50 750 Td"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({escaped}) Tj T*")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def build_pdf(pages, compress=False, kids_per_node=None, extra_objects=None):
    """pages: list of lists of content-stream byte strings (a page may have several streams).

    Returns PDF bytes. The page tree is flat unless kids_per_node is set.
    """
    objects = {}
    next_id = [4]

    def new(body):
        oid = next_id[0]
        next_id[0] += 1
        objects[oid] = body
        return oid

    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    page_ids = []
    for streams in pages:
        ids = []
        for data in streams:
            if compress:
                payload = zlib.compress(data, 9)
                head = f"<< /Length {len(payload)} /Filter /FlateDecode >>".encode()
            else:
                payload = data
                head = f"<< /Length {len(payload)} >>".encode()
            ids.append(new(head + b"\nstream\n" + payload + b"\nendstream"))
        contents = (f"{ids[0]} 0 R" if len(ids) == 1 else "[" + " ".join(f"{i} 0 R" for i in ids) + "]") if ids else None
        body = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >>"
        if contents:
            body += f" /Contents {contents}"
        body += " >>"
        page_ids.append(new(body.encode()))
    if kids_per_node:
        groups = [page_ids[i:i + kids_per_node] for i in range(0, len(page_ids), kids_per_node)]
        node_ids = []
        for group in groups:
            node_ids.append(new(("<< /Type /Pages /Parent 2 0 R /Count %d /Kids [%s] >>" % (
                len(group), " ".join(f"{g} 0 R" for g in group))).encode()))
        kids = node_ids
    else:
        kids = page_ids
    objects[2] = ("<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), " ".join(f"{k} 0 R" for k in kids))).encode()
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    for body in (extra_objects or []):
        new(body)
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + objects[oid] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for oid in range(1, size):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def simple_pdf(page_lines, compress=False):
    """One text stream per page; page_lines is a list of line-lists."""
    return build_pdf([[text_stream(lines)] for lines in page_lines], compress=compress)


def row_page(tag, value, unit="bar", field="pressure"):
    return [f"{tag} | {field} | {value} {unit}"]
