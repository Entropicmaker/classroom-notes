#!/usr/bin/env python3
"""Extract PPTX text, tables, pictures, notes, chart caches, and review warnings."""

import argparse
import hashlib
import json
import posixpath
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET


NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}


def paragraphs(element):
    return [
        "".join(token.text or "" for token in paragraph.findall(".//a:t", NS))
        for paragraph in element.findall(".//a:p", NS)
    ]


def bounds(element):
    transform = element.find(".//a:xfrm", NS)
    if transform is None:
        return {}
    return {
        name: dict(value.attrib)
        for name in ("off", "ext")
        if (value := transform.find(f"a:{name}", NS)) is not None
    }


def relationship_path(part):
    return posixpath.join(
        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
    )


def relationships(archive, part):
    rel_part = relationship_path(part)
    if rel_part not in archive.namelist():
        return {}
    result = {}
    root = ET.fromstring(archive.read(rel_part))
    for relation in root:
        if relation.attrib.get("TargetMode") == "External":
            continue
        result[relation.attrib["Id"]] = {
            "target": posixpath.normpath(
                posixpath.join(posixpath.dirname(part), relation.attrib["Target"])
            ),
            "type": relation.attrib.get("Type", "").rsplit("/", 1)[-1],
        }
    return result


def note_paragraphs(archive, relation_map):
    notes = []
    for relation in relation_map.values():
        if relation["type"] != "notesSlide" or relation["target"] not in archive.namelist():
            continue
        root = ET.fromstring(archive.read(relation["target"]))
        for shape in root.findall(".//p:sp", NS):
            placeholder = shape.find(".//p:nvPr/p:ph", NS)
            placeholder_type = "" if placeholder is None else placeholder.attrib.get("type", "")
            if placeholder_type in {"dt", "ftr", "hdr", "sldNum"}:
                continue
            notes.extend(text for text in paragraphs(shape) if text.strip())
    return notes


def chart_payload(archive, relation):
    target = relation["target"]
    if target not in archive.namelist():
        return {"target": target, "warning": "Chart relationship target missing"}
    root = ET.fromstring(archive.read(target))
    texts = [node.text or "" for node in root.findall(".//a:t", NS) if (node.text or "").strip()]
    values = [node.text or "" for node in root.findall(".//c:v", NS) if (node.text or "").strip()]
    formulas = [node.text or "" for node in root.findall(".//c:f", NS) if (node.text or "").strip()]
    return {"target": target, "text": texts, "values": values, "formulas": formulas}


def diagram_payload(archive, relation):
    target = relation["target"]
    if target not in archive.namelist():
        return {"target": target, "warning": "Diagram relationship target missing"}
    root = ET.fromstring(archive.read(target))
    texts = [node.text or "" for node in root.findall(".//a:t", NS) if (node.text or "").strip()]
    return {"target": target, "text": texts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source missing: {source}")
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Output already populated; choose a new work directory")
    (out / "assets").mkdir()

    result = {
        "source": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "slides": [],
    }
    markdown = []
    seen_images = {}

    with zipfile.ZipFile(source) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        presentation_rels = relationships(archive, "ppt/presentation.xml")
        order = [
            presentation_rels[item.attrib[f"{{{NS['r']}}}id"]]["target"]
            for item in presentation.findall("p:sldIdLst/p:sldId", NS)
        ]

        for number, part in enumerate(order, 1):
            root = ET.fromstring(archive.read(part))
            relation_map = relationships(archive, part)
            slide = {
                "number": number,
                "part": part,
                "paragraphs": paragraphs(root),
                "tables": [],
                "pictures": [],
                "text_shapes": [],
                "notes": note_paragraphs(archive, relation_map),
                "charts": [],
                "diagrams": [],
                "math_tokens": [
                    node.text or "" for node in root.findall(".//m:t", NS) if (node.text or "").strip()
                ],
                "warnings": [],
            }

            for shape in root.findall(".//p:sp", NS):
                texts = paragraphs(shape)
                if any(text.strip() for text in texts):
                    slide["text_shapes"].append({"paragraphs": texts, "bounds": bounds(shape)})

            for table in root.findall(".//a:tbl", NS):
                slide["tables"].append(
                    [
                        [" / ".join(paragraphs(cell)) for cell in row.findall("a:tc", NS)]
                        for row in table.findall("a:tr", NS)
                    ]
                )

            for index, picture in enumerate(root.findall(".//p:pic", NS), 1):
                blip = picture.find(".//a:blip", NS)
                rid = None if blip is None else blip.attrib.get(f"{{{NS['r']}}}embed")
                relation = relation_map.get(rid)
                if not relation or relation["target"] not in archive.namelist():
                    slide["pictures"].append(
                        {"warning": "Linked or unavailable picture", "bounds": bounds(picture)}
                    )
                    continue
                target = relation["target"]
                if not target.startswith("ppt/media/"):
                    slide["pictures"].append(
                        {"warning": "Unsupported image relationship", "target": target}
                    )
                    continue
                data = archive.read(target)
                digest = hashlib.sha256(data).hexdigest()
                extension = Path(target).suffix.lower()
                if digest not in seen_images:
                    filename = f"s{number:03}-img{index:02}-{digest[:8]}{extension}"
                    path = out / "assets" / filename
                    with path.open("xb") as handle:
                        handle.write(data)
                    seen_images[digest] = f"assets/{filename}"
                crop = picture.find(".//a:srcRect", NS)
                slide["pictures"].append(
                    {
                        "path": seen_images[digest],
                        "sha256": digest,
                        "bounds": bounds(picture),
                        "crop": None if crop is None else dict(crop.attrib),
                    }
                )

            for relation in relation_map.values():
                if relation["type"] == "chart":
                    slide["charts"].append(chart_payload(archive, relation))
                elif relation["type"] == "diagramData":
                    slide["diagrams"].append(diagram_payload(archive, relation))
                elif relation["type"] in {"oleObject", "package", "audio", "video"}:
                    slide["warnings"].append(
                        f"Contains {relation['type']} object; inspect the rendered slide and source application."
                    )

            if root.findall(".//{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"):
                slide["warnings"].append("Contains AlternateContent; extraction may omit a fallback branch.")

            markdown.extend(["", f"## 幻灯片 {number}", ""])
            markdown.extend(text for text in slide["paragraphs"] if text.strip())
            for table in slide["tables"]:
                markdown.extend(["", "表格逐行（各单元格以 | 分隔）："])
                markdown.extend(" | ".join(row) for row in table)
            if slide["notes"]:
                markdown.extend(["", "演讲者备注：", *slide["notes"]])
            if slide["charts"]:
                markdown.extend(["", "图表缓存数据（仍须看渲染页核对）："])
                markdown.extend(json.dumps(item, ensure_ascii=False) for item in slide["charts"])
            if slide["diagrams"]:
                markdown.extend(["", "SmartArt/图示文本（仍须看渲染页核对）："])
                markdown.extend(json.dumps(item, ensure_ascii=False) for item in slide["diagrams"])
            if slide["math_tokens"]:
                markdown.extend(["", "公式文本片段（需看渲染页恢复结构）：", " ".join(slide["math_tokens"])])
            if slide["pictures"]:
                markdown.extend(["", "图片："])
                markdown.extend(item.get("path", item.get("warning", "")) for item in slide["pictures"])
            if slide["warnings"]:
                markdown.extend(["", "提取警告：", *slide["warnings"]])
            result["slides"].append(slide)

    source_md = out / "source.md"
    manifest = out / "manifest.json"
    source_md.write_text("\n".join(markdown).lstrip() + "\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "slides": len(result["slides"]),
                "unique_images": len(seen_images),
                "slides_with_notes": sum(bool(item["notes"]) for item in result["slides"]),
                "text": str(source_md),
                "manifest": str(manifest),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error))
