from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import io
from typing import List, Dict, Any, Optional, Union
import logging
import json
import re
import math

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vector-Layer Extraction API",
    description="Extracts layers and associated vector data from PDF files",
    version="1.0.0"
)

# Utility functions
def point_to_dict(p, precision: int = 2) -> dict:
    if p is None: return {"x": 0.0, "y": 0.0}
    try:
        if hasattr(p, 'x') and hasattr(p, 'y'):
            return {"x": round(float(p.x), precision), "y": round(float(p.y), precision)}
        elif isinstance(p, (tuple, list)) and len(p) >= 2:
            return {"x": round(float(p[0]), precision), "y": round(float(p[1]), precision)}
        return {"x": 0.0, "y": 0.0}
    except (ValueError, TypeError, AttributeError): return {"x": 0.0, "y": 0.0}

def rect_to_dict(r, precision: int = 2) -> dict:
    if r is None: return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0, "width": 0.0, "height": 0.0}
    try:
        if isinstance(r, tuple) and len(r) >= 4:
            return {"x0": round(float(r[0]), precision), "y0": round(float(r[1]), precision),
                    "x1": round(float(r[2]), precision), "y1": round(float(r[3]), precision),
                    "width": round(float(r[2]) - float(r[0]), precision),
                    "height": round(float(r[3]) - float(r[1]), precision)}
        elif hasattr(r, 'x0'):
            return {"x0": round(float(r.x0), precision), "y0": round(float(r.y0), precision),
                    "x1": round(float(r.x1), precision), "y1": round(float(r.y1), precision),
                    "width": round(float(r.width), precision), "height": round(float(r.height), precision)}
        return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0, "width": 0.0, "height": 0.0}
    except (ValueError, TypeError, AttributeError): return {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0, "width": 0.0, "height": 0.0}

def distance(p1: dict, p2: dict) -> float:
    return math.sqrt((p2['x'] - p1['x'])**2 + (p2['y'] - p1['y'])**2)

def extract_dimension_info(text: str) -> Dict[str, Any]:
    dimension_data = {"is_dimension": False, "value": None, "unit": None, "type": None}
    patterns = [(r'^(\d+)$', 'numeric'), (r'(\d+(?:\.\d+)?)\s*(mm|cm|m|ft|in|")', 'measurement'),
                (r'(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)', 'dimension'),
                (r'[ØΦ]\s*(\d+(?:\.\d+)?)', 'diameter'), (r'R\s*(\d+(?:\.\d+)?)', 'radius'),
                (r'∠\s*(\d+(?:\.\d+)?)[°]?', 'angle'), (r'(\d+)\s*:\s*(\d+)', 'scale'),
                (r'±\s*(\d+(?:\.\d+)?)', 'tolerance')]
    for pattern, dim_type in patterns:
        match = re.search(pattern, text.strip())
        if match:
            dimension_data["is_dimension"] = True
            dimension_data["value"] = match.group(1)
            dimension_data["type"] = dim_type
            if len(match.groups()) > 1: dimension_data["unit"] = match.group(2)
            break
    return dimension_data

def extract_path_data(path: dict, precision: int = 2) -> Dict[str, Any]:
    width = path.get("width", 1.0) or 1.0; width = float(width) if isinstance(width, (int, float)) else 1.0
    opacity = path.get("opacity", 1.0) or 1.0; opacity = float(opacity) if isinstance(opacity, (int, float)) else 1.0
    path_data = {"width": round(width, precision), "opacity": round(opacity, precision), "closePath": path.get("closePath", False)}
    if "color" in path and path["color"]: path_data["color"] = [round(float(c), 3) for c in path["color"]] if all(isinstance(c, (int, float)) for c in path["color"]) else []
    return path_data

def extract_vector_data(page: fitz.Page, precision: int = 2) -> List[Dict[str, Any]]:
    lines = []
    try:
        drawings = page.get_drawings()
        for path in drawings:
            path_info = extract_path_data(path, precision)
            for item in path["items"]:
                item_type, points = item[0], item[1:] if len(item) > 1 else []
                if item_type == "l" and len(points) >= 2:
                    p1, p2 = point_to_dict(points[0], precision), point_to_dict(points[1], precision)
                    line_length = distance(p1, p2)
                    lines.append({"type": "line", "p1": p1, "p2": p2, "length": round(line_length, precision), **path_info})
    except Exception as e: logger.warning(f"Error extracting vector data: {e}")
    return lines

# Layer extraction functions (simplified from layer code)
def extract_ocg_catalog_info(pdf_doc: fitz.Document) -> Dict[str, Any]:
    ocg_info = {"ocgs": {}, "default_config": {}, "alternate_configs": []}
    try:
        catalog = pdf_doc.pdf_catalog()
        if 'OCProperties' in catalog:
            oc_props = catalog['OCProperties']
            if 'OCGs' in oc_props:
                for ocg_ref in oc_props['OCGs']:
                    ocg_obj = pdf_doc.xref_get_object(ocg_ref)
                    ocg_data = {"name": f"Layer_{ocg_ref}", "visible": True, "locked": False, "intent": [], "usage": {}, "creator_info": ""}
                    if ocg_obj: ocg_data.update(parse_ocg_object(ocg_obj))
                    ocg_info['ocgs'][str(ocg_ref)] = ocg_data
    except Exception: logger.debug("Could not extract OCG catalog info")
    return ocg_info

def parse_ocg_object(ocg_obj: str) -> Dict[str, Any]:
    ocg_data = {"name": "Unknown Layer", "visible": True, "locked": False, "intent": [], "usage": {}, "creator_info": ""}
    try:
        if isinstance(ocg_obj, str):
            name_match = re.search(r'/Name\s*\((.*?)\)', ocg_obj)
            ocg_data["name"] = name_match.group(1) if name_match else ocg_data["name"]
    except Exception: logger.debug("Error parsing OCG object")
    return ocg_data

def extract_layers_from_xref(pdf_doc: fitz.Document) -> List[Dict[str, Any]]:
    layers = []
    try:
        for xref in range(pdf_doc.xref_length()):
            obj_type = pdf_doc.xref_get_key(xref, "Type")
            if obj_type and "OCG" in str(obj_type):
                name_obj = pdf_doc.xref_get_key(xref, "Name")
                layer_name = str(name_obj).strip('()/"') if name_obj else f"Layer_{xref}"
                layers.append({"name": layer_name, "visible": True, "locked": False, "ocg_ref": str(xref), "xref_number": xref})
    except Exception: logger.debug("Error in xref layer extraction")
    return layers

def extract_layer_configurations(pdf_doc: fitz.Document) -> List[Dict[str, Any]]:
    return [{"name": "Default Configuration", "base_state": "ON", "creator": pdf_doc.metadata.get('creator', 'Unknown')}]

def analyze_pages_for_layers(pdf_doc: fitz.Document) -> tuple:
    pages_with_layers = []
    for page_num in range(pdf_doc.page_count):
        page = pdf_doc[page_num]
        if page.get_layers(): pages_with_layers.append({"page_number": page_num + 1, "layers": [l.name for l in pdf_doc.get_ocgs()]})
    return pages_with_layers, {}

def calculate_layer_usage_stats(layers: List[Dict], pages_with_layers: List[Dict], total_pages: int) -> Dict[str, Any]:
    return {"total_layers": len(layers), "pages_with_layers": len(pages_with_layers), "pages_without_layers": total_pages - len(pages_with_layers)}

def extract_layers_and_vectors(pdf_doc: fitz.Document) -> Dict[str, Any]:
    layers = extract_layers_from_xref(pdf_doc)
    ocg_info = extract_ocg_catalog_info(pdf_doc)
    layer_configurations = extract_layer_configurations(pdf_doc)
    pages_with_layers, _ = analyze_pages_for_layers(pdf_doc)
    
    for layer_data in layers:
        ocg_ref = layer_data.get("ocg_ref")
        if ocg_ref:
            layer_vectors = {}
            for page_num in range(pdf_doc.page_count):
                page = pdf_doc[page_num]
                for ocg in pdf_doc.get_ocgs():
                    ocg.set_state(str(ocg.ocg_ref) == ocg_ref)
                page_vectors = extract_vector_data(page)
                if page_vectors: layer_vectors[f"page_{page_num + 1}"] = page_vectors
            layer_data["vectors"] = layer_vectors
            layer_data["vector_count"] = sum(len(v) for v in layer_vectors.values())
    
    stats = calculate_layer_usage_stats(layers, pages_with_layers, pdf_doc.page_count)
    
    return {
        "has_layers": len(layers) > 0,
        "layer_count": len(layers),
        "layers": layers,
        "ocg_info": ocg_info,
        "layer_configurations": layer_configurations,
        "pages_with_layers": pages_with_layers,
        "total_pages": pdf_doc.page_count,
        "pdf_version": getattr(pdf_doc, 'pdf_version', 'Unknown'),
        "creator": pdf_doc.metadata.get('creator', 'Unknown'),
        "producer": pdf_doc.metadata.get('producer', 'Unknown'),
        "layer_usage_analysis": stats
    }

@app.post("/extract-layers-and-vectors")
async def extract_layers_and_vectors(file: UploadFile = File(...), precision: int = Query(2, ge=0, le=3, description="Decimal precision for coordinates")):
    """Extract layers and associated vector lines from PDF"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        content = await file.read()
        pdf_doc = fitz.open(stream=content, filetype="pdf")
        layer_data = extract_layers_and_vectors(pdf_doc)
        pdf_doc.close()
        return JSONResponse(content=layer_data)
    except fitz.fitz.FileDataError:
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")

@app.get("/")
async def root(): return {"message": "Vector-Layer Extraction API v1.0 is running"}

@app.get("/health")
async def health_check(): return {"status": "healthy", "service": "vector-layer-extractor", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
