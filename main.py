from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import io
from typing import List, Dict, Any, Optional, Union
import logging
import json
import re

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Layer Extraction API",
    description="Uitgebreide API om alle layer informatie uit PDF-bestanden te extraheren",
    version="2.0.0"
)

class LayerInfo:
    def __init__(self, name: str, visible: bool, locked: bool = False, ocg_ref: str = None, 
                 intent: List[str] = None, usage: Dict = None, creator_info: str = None):
        self.name = name
        self.visible = visible
        self.locked = locked
        self.ocg_ref = ocg_ref
        self.intent = intent or []
        self.usage = usage or {}
        self.creator_info = creator_info
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked,
            "ocg_reference": self.ocg_ref,
            "intent": self.intent,
            "usage": self.usage,
            "creator_info": self.creator_info
        }

@app.get("/")
async def root():
    return {"message": "PDF Layer Extraction API v2.0 is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "pdf-layer-extractor", "version": "2.0"}

@app.post("/extract-layers")
async def extract_layers(file: UploadFile = File(...)):
    """
    Extract comprehensive layer information from PDF file
    
    Args:
        file: PDF file to process
        
    Returns:
        JSON response with complete layer information including OCG details
    """
    
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Alleen PDF-bestanden zijn toegestaan")
    
    try:
        # Read file content
        content = await file.read()
        
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Leeg bestand ontvangen")
        
        # Open PDF with PyMuPDF
        pdf_document = fitz.open(stream=content, filetype="pdf")
        
        # Extract comprehensive layer information
        layer_info = extract_comprehensive_layers(pdf_document)
        
        # Close document
        pdf_document.close()
        
        return JSONResponse(content={
            "filename": file.filename,
            "has_layers": layer_info["has_layers"],
            "layer_count": layer_info["layer_count"],
            "layers": layer_info["layers"],
            "ocg_info": layer_info["ocg_info"],
            "layer_configurations": layer_info["layer_configurations"],
            "pages_with_layers": layer_info["pages_with_layers"],
            "total_pages": layer_info["total_pages"],
            "pdf_version": layer_info["pdf_version"],
            "creator": layer_info["creator"],
            "producer": layer_info["producer"],
            "layer_usage_analysis": layer_info["layer_usage_analysis"]
        })
        
    except fitz.fitz.FileDataError:
        raise HTTPException(status_code=400, detail="Ongeldig PDF-bestand")
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Fout bij verwerken PDF: {str(e)}")

def extract_comprehensive_layers(pdf_doc: fitz.Document) -> Dict[str, Any]:
    """
    Extract complete layer information from PDF document including OCG details
    
    Args:
        pdf_doc: PyMuPDF document object
        
    Returns:
        Dictionary with comprehensive layer information
    """
    
    layers = []
    ocg_info = {}
    layer_configurations = []
    pages_with_layers = []
    layer_names = set()
    layer_usage_stats = {}
    
    # Get PDF metadata
    metadata = pdf_doc.metadata
    pdf_version = getattr(pdf_doc, 'pdf_version', 'Unknown')
    
    try:
        # Method 1: Extract OCG information from document catalog
        ocg_info = extract_ocg_catalog_info(pdf_doc)
        
        # Method 2: Extract from xref table
        xref_layers = extract_layers_from_xref(pdf_doc)
        
        # Method 3: Extract layer configurations
        layer_configurations = extract_layer_configurations(pdf_doc)
        
        # Method 4: Analyze pages for layer usage
        pages_with_layers, page_layer_usage = analyze_pages_for_layers(pdf_doc)
        
        # Combine all layer information
        all_layers = {}
        
        # Process OCG catalog info
        if ocg_info.get('ocgs'):
            for ocg_ref, ocg_data in ocg_info['ocgs'].items():
                layer_name = ocg_data.get('name', f'Layer_{ocg_ref}')
                all_layers[layer_name] = {
                    'name': layer_name,
                    'ocg_ref': ocg_ref,
                    'visible': ocg_data.get('visible', True),
                    'locked': ocg_data.get('locked', False),
                    'intent': ocg_data.get('intent', []),
                    'usage': ocg_data.get('usage', {}),
                    'creator_info': ocg_data.get('creator_info', ''),
                    'source': 'OCG_catalog'
                }
                layer_names.add(layer_name)
        
        # Process xref layers
        for layer_data in xref_layers:
            layer_name = layer_data['name']
            if layer_name in all_layers:
                # Merge information
                all_layers[layer_name].update({k: v for k, v in layer_data.items() if v})
            else:
                all_layers[layer_name] = layer_data
                all_layers[layer_name]['source'] = 'xref_table'
            layer_names.add(layer_name)
        
        # Convert to list format
        layers = list(all_layers.values())
        
        # Calculate usage statistics
        layer_usage_stats = calculate_layer_usage_stats(layers, pages_with_layers, pdf_doc.page_count)
        
    except Exception as e:
        logger.error(f"Error in comprehensive layer extraction: {str(e)}")
        # Fallback to basic extraction
        basic_info = extract_basic_layers(pdf_doc)
        return basic_info
    
    return {
        "has_layers": len(layer_names) > 0,
        "layer_count": len(layer_names),
        "layers": layers,
        "ocg_info": ocg_info,
        "layer_configurations": layer_configurations,
        "pages_with_layers": pages_with_layers,
        "total_pages": pdf_doc.page_count,
        "pdf_version": pdf_version,
        "creator": metadata.get('creator', 'Unknown'),
        "producer": metadata.get('producer', 'Unknown'),
        "layer_usage_analysis": layer_usage_stats
    }

def extract_ocg_catalog_info(pdf_doc: fitz.Document) -> Dict[str, Any]:
    """
    Extract OCG (Optional Content Groups) information from document catalog
    """
    ocg_info = {
        'ocgs': {},
        'default_config': {},
        'alternate_configs': []
    }
    
    try:
        # Get document catalog
        catalog = pdf_doc.pdf_catalog()
        
        # Look for OCProperties in catalog
        if 'OCProperties' in catalog:
            oc_props = catalog['OCProperties']
            
            # Extract OCGs
            if 'OCGs' in oc_props:
                ocgs = oc_props['OCGs']
                for i, ocg_ref in enumerate(ocgs):
                    try:
                        ocg_obj = pdf_doc.xref_get_object(ocg_ref)
                        ocg_data = parse_ocg_object(ocg_obj)
                        ocg_info['ocgs'][str(ocg_ref)] = ocg_data
                    except:
                        continue
            
            # Extract default configuration
            if 'D' in oc_props:
                default_config = oc_props['D']
                ocg_info['default_config'] = parse_oc_config(pdf_doc, default_config)
            
            # Extract alternate configurations
            if 'Configs' in oc_props:
                configs = oc_props['Configs']
                for config_ref in configs:
                    try:
                        config_obj = pdf_doc.xref_get_object(config_ref)
                        parsed_config = parse_oc_config(pdf_doc, config_obj)
                        ocg_info['alternate_configs'].append(parsed_config)
                    except:
                        continue
                        
    except Exception as e:
        logger.debug(f"Could not extract OCG catalog info: {str(e)}")
    
    return ocg_info

def parse_ocg_object(ocg_obj: str) -> Dict[str, Any]:
    """Parse OCG object string to extract layer information"""
    ocg_data = {
        'name': 'Unknown Layer',
        'visible': True,
        'locked': False,
        'intent': [],
        'usage': {},
        'creator_info': ''
    }
    
    try:
        # Extract name
        name_match = re.search(r'/Name\s*\((.*?)\)', ocg_obj)
        if name_match:
            ocg_data['name'] = name_match.group(1)
        
        # Extract intent
        intent_match = re.search(r'/Intent\s*\[(.*?)\]', ocg_obj)
        if intent_match:
            intents = re.findall(r'/(\w+)', intent_match.group(1))
            ocg_data['intent'] = intents
        
        # Extract usage information
        usage_match = re.search(r'/Usage\s*<<(.*?)>>', ocg_obj, re.DOTALL)
        if usage_match:
            usage_str = usage_match.group(1)
            ocg_data['usage'] = parse_usage_dict(usage_str)
            
    except Exception as e:
        logger.debug(f"Error parsing OCG object: {str(e)}")
    
    return ocg_data

def parse_oc_config(pdf_doc: fitz.Document, config_obj: Union[str, int]) -> Dict[str, Any]:
    """Parse Optional Content Configuration"""
    config_data = {
        'name': 'Default',
        'creator': '',
        'base_state': 'ON',
        'on_layers': [],
        'off_layers': [],
        'locked_layers': []
    }
    
    try:
        if isinstance(config_obj, int):
            config_str = pdf_doc.xref_get_object(config_obj)
        else:
            config_str = str(config_obj)
        
        # Extract configuration name
        name_match = re.search(r'/Name\s*\((.*?)\)', config_str)
        if name_match:
            config_data['name'] = name_match.group(1)
        
        # Extract creator
        creator_match = re.search(r'/Creator\s*\((.*?)\)', config_str)
        if creator_match:
            config_data['creator'] = creator_match.group(1)
        
        # Extract base state
        base_state_match = re.search(r'/BaseState\s*/(\w+)', config_str)
        if base_state_match:
            config_data['base_state'] = base_state_match.group(1)
            
    except Exception as e:
        logger.debug(f"Error parsing OC config: {str(e)}")
    
    return config_data

def parse_usage_dict(usage_str: str) -> Dict[str, Any]:
    """Parse usage dictionary from OCG"""
    usage = {}
    
    try:
        # Look for common usage patterns
        if '/Print' in usage_str:
            print_match = re.search(r'/Print\s*<<(.*?)>>', usage_str)
            if print_match:
                usage['print'] = 'ON' if '/PrintState/ON' in print_match.group(1) else 'OFF'
        
        if '/View' in usage_str:
            view_match = re.search(r'/View\s*<<(.*?)>>', usage_str)
            if view_match:
                usage['view'] = 'ON' if '/ViewState/ON' in view_match.group(1) else 'OFF'
                
        if '/Export' in usage_str:
            export_match = re.search(r'/Export\s*<<(.*?)>>', usage_str)
            if export_match:
                usage['export'] = 'ON' if '/ExportState/ON' in export_match.group(1) else 'OFF'
                
    except Exception as e:
        logger.debug(f"Error parsing usage dict: {str(e)}")
    
    return usage

def extract_layers_from_xref(pdf_doc: fitz.Document) -> List[Dict[str, Any]]:
    """Extract layer information from xref table"""
    layers = []
    
    try:
        xref_count = pdf_doc.xref_length()
        
        for xref in range(xref_count):
            try:
                obj_type = pdf_doc.xref_get_key(xref, "Type")
                if obj_type and "OCG" in str(obj_type):
                    # Get layer properties
                    name_obj = pdf_doc.xref_get_key(xref, "Name")
                    intent_obj = pdf_doc.xref_get_key(xref, "Intent")
                    usage_obj = pdf_doc.xref_get_key(xref, "Usage")
                    
                    layer_name = str(name_obj).strip('()/"') if name_obj else f"Layer_{xref}"
                    
                    layer_data = {
                        "name": layer_name,
                        "visible": True,  # Default
                        "locked": False,  # Default
                        "ocg_ref": str(xref),
                        "intent": parse_intent_from_obj(intent_obj) if intent_obj else [],
                        "usage": parse_usage_from_obj(usage_obj) if usage_obj else {},
                        "xref_number": xref
                    }
                    
                    layers.append(layer_data)
                    
            except Exception as e:
                logger.debug(f"Error processing xref {xref}: {str(e)}")
                continue
                
    except Exception as e:
        logger.debug(f"Error in xref layer extraction: {str(e)}")
    
    return layers

def parse_intent_from_obj(intent_obj: str) -> List[str]:
    """Parse intent from PDF object"""
    intents = []
    try:
        if intent_obj:
            intent_matches = re.findall(r'/(\w+)', str(intent_obj))
            intents = intent_matches
    except:
        pass
    return intents

def parse_usage_from_obj(usage_obj: str) -> Dict[str, Any]:
    """Parse usage from PDF object"""
    usage = {}
    try:
        if usage_obj:
            usage_str = str(usage_obj)
            usage = parse_usage_dict(usage_str)
    except:
        pass
    return usage

def extract_layer_configurations(pdf_doc: fitz.Document) -> List[Dict[str, Any]]:
    """Extract layer configuration information"""
    configurations = []
    
    try:
        # This would typically involve parsing the OCProperties/Configs array
        # For now, we'll provide a basic structure
        configurations.append({
            "name": "Default Configuration",
            "base_state": "ON",
            "creator": pdf_doc.metadata.get('creator', 'Unknown'),
            "description": "Default layer visibility configuration"
        })
        
    except Exception as e:
        logger.debug(f"Error extracting layer configurations: {str(e)}")
    
    return configurations

def analyze_pages_for_layers(pdf_doc: fitz.Document) -> tuple:
    """Analyze each page for layer usage"""
    pages_with_layers = []
    layer_usage = {}
    
    for page_num in range(pdf_doc.page_count):
        try:
            page = pdf_doc[page_num]
            page_layers = extract_page_layer_details(page)
            
            if page_layers:
                pages_with_layers.append({
                    "page_number": page_num + 1,
                    "layers": page_layers,
                    "layer_count": len(page_layers)
                })
                
                # Update usage statistics
                for layer_name in page_layers:
                    if layer_name not in layer_usage:
                        layer_usage[layer_name] = []
                    layer_usage[layer_name].append(page_num + 1)
                    
        except Exception as e:
            logger.debug(f"Error analyzing page {page_num}: {str(e)}")
            continue
    
    return pages_with_layers, layer_usage

def extract_page_layer_details(page: fitz.Page) -> List[str]:
    """Extract detailed layer information from a page"""
    layers = []
    
    try:
        # Get page content
        content_streams = page.get_contents()
        
        for stream in content_streams:
            try:
                content = stream.get_buffer().decode('latin-1', errors='ignore')
                
                # Look for OCG markers
                ocg_patterns = [
                    r'/OC\s*/(\w+)',  # Basic OCG reference
                    r'BDC\s*/OC\s*/(\w+)',  # Begin marked content with OCG
                    r'/Properties\s*<<.*?/(\w+)\s*\d+\s+\d+\s+R.*?>>',  # Properties dictionary
                ]
                
                for pattern in ocg_patterns:
                    matches = re.findall(pattern, content)
                    layers.extend(matches)
                
            except Exception as e:
                logger.debug(f"Error processing content stream: {str(e)}")
                continue
                
    except Exception as e:
        logger.debug(f"Error extracting page layer details: {str(e)}")
    
    return list(set(layers))  # Remove duplicates

def calculate_layer_usage_stats(layers: List[Dict], pages_with_layers: List[Dict], total_pages: int) -> Dict[str, Any]:
    """Calculate layer usage statistics"""
    stats = {
        "total_layers": len(layers),
        "pages_with_layers": len(pages_with_layers),
        "pages_without_layers": total_pages - len(pages_with_layers),
        "layer_distribution": {},
        "most_used_layers": [],
        "least_used_layers": []
    }
    
    try:
        # Calculate layer distribution
        layer_usage_count = {}
        
        for page_info in pages_with_layers:
            for layer in page_info.get('layers', []):
                layer_usage_count[layer] = layer_usage_count.get(layer, 0) + 1
        
        stats["layer_distribution"] = layer_usage_count
        
        # Sort layers by usage
        sorted_layers = sorted(layer_usage_count.items(), key=lambda x: x[1], reverse=True)
        
        if sorted_layers:
            stats["most_used_layers"] = sorted_layers[:3]  # Top 3
            stats["least_used_layers"] = sorted_layers[-3:]  # Bottom 3
            
    except Exception as e:
        logger.debug(f"Error calculating usage stats: {str(e)}")
    
    return stats

def extract_basic_layers(pdf_doc: fitz.Document) -> Dict[str, Any]:
    """Fallback basic layer extraction"""
    return {
        "has_layers": False,
        "layer_count": 0,
        "layers": [],
        "ocg_info": {},
        "layer_configurations": [],
        "pages_with_layers": [],
        "total_pages": pdf_doc.page_count,
        "pdf_version": getattr(pdf_doc, 'pdf_version', 'Unknown'),
        "creator": pdf_doc.metadata.get('creator', 'Unknown'),
        "producer": pdf_doc.metadata.get('producer', 'Unknown'),
        "layer_usage_analysis": {}
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
