from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import io
from typing import List, Dict, Any, Optional
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Layer Extraction API",
    description="API om layers uit PDF-bestanden te extraheren",
    version="1.0.0"
)

class LayerInfo:
    def __init__(self, name: str, visible: bool, locked: bool = False):
        self.name = name
        self.visible = visible
        self.locked = locked
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked
        }

@app.get("/")
async def root():
    return {"message": "PDF Layer Extraction API is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "pdf-layer-extractor"}

@app.post("/extract-layers")
async def extract_layers(file: UploadFile = File(...)):
    """
    Extract layers from PDF file
    
    Args:
        file: PDF file to process
        
    Returns:
        JSON response with layer information
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
        
        # Extract layer information
        layer_info = extract_pdf_layers(pdf_document)
        
        # Close document
        pdf_document.close()
        
        return JSONResponse(content={
            "filename": file.filename,
            "has_layers": layer_info["has_layers"],
            "layer_count": layer_info["layer_count"],
            "layers": layer_info["layers"],
            "pages_with_layers": layer_info["pages_with_layers"],
            "total_pages": layer_info["total_pages"]
        })
        
    except fitz.fitz.FileDataError:
        raise HTTPException(status_code=400, detail="Ongeldig PDF-bestand")
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Fout bij verwerken PDF: {str(e)}")

def extract_pdf_layers(pdf_doc: fitz.Document) -> Dict[str, Any]:
    """
    Extract layer information from PDF document
    
    Args:
        pdf_doc: PyMuPDF document object
        
    Returns:
        Dictionary with layer information
    """
    
    layers = []
    pages_with_layers = []
    layer_names = set()
    
    try:
        # Check if document has OCG (Optional Content Groups) - PDF layers
        if hasattr(pdf_doc, 'layer_configs') and pdf_doc.layer_configs:
            # Get layer configurations
            for config in pdf_doc.layer_configs:
                if 'OCGs' in config:
                    for ocg in config['OCGs']:
                        layer_name = ocg.get('name', 'Unnamed Layer')
                        layer_visible = ocg.get('on', True)
                        layer_locked = ocg.get('locked', False)
                        
                        layer_info = LayerInfo(layer_name, layer_visible, layer_locked)
                        layers.append(layer_info.to_dict())
                        layer_names.add(layer_name)
        
        # Alternative method: Check each page for layer information
        for page_num in range(pdf_doc.page_count):
            page = pdf_doc[page_num]
            
            # Get drawing commands that might contain layer info
            try:
                # Check if page has any Optional Content references
                page_dict = page.get_contents()
                if page_dict:
                    # Look for OCG references in page content
                    page_layers = extract_page_layers(page)
                    if page_layers:
                        pages_with_layers.append({
                            "page_number": page_num + 1,
                            "layers": page_layers
                        })
                        for layer in page_layers:
                            layer_names.add(layer)
            except:
                continue
    
    except Exception as e:
        logger.warning(f"Could not extract layer configs: {str(e)}")
    
    # If no layers found through OCG, try alternative method
    if not layers and not layer_names:
        layers, layer_names = extract_layers_alternative_method(pdf_doc)
    
    return {
        "has_layers": len(layer_names) > 0,
        "layer_count": len(layer_names),
        "layers": layers if layers else list(layer_names),
        "pages_with_layers": pages_with_layers,
        "total_pages": pdf_doc.page_count
    }

def extract_page_layers(page: fitz.Page) -> List[str]:
    """
    Extract layer names from a specific page
    
    Args:
        page: PyMuPDF page object
        
    Returns:
        List of layer names found on the page
    """
    layers = []
    
    try:
        # Get page's resource dictionary
        resources = page.get_contents()
        if resources:
            # Look for OCG references in the content stream
            content = resources[0].get_buffer().decode('latin-1', errors='ignore')
            
            # Simple pattern matching for OCG references
            import re
            ocg_pattern = r'/OC\s*/(\w+)'
            matches = re.findall(ocg_pattern, content)
            layers.extend(matches)
            
    except Exception as e:
        logger.debug(f"Could not extract layers from page: {str(e)}")
    
    return list(set(layers))  # Remove duplicates

def extract_layers_alternative_method(pdf_doc: fitz.Document) -> tuple:
    """
    Alternative method to extract layers using document structure
    
    Args:
        pdf_doc: PyMuPDF document object
        
    Returns:
        Tuple of (layers list, layer names set)
    """
    layers = []
    layer_names = set()
    
    try:
        # Get document's xref table
        xref_count = pdf_doc.xref_length()
        
        for xref in range(xref_count):
            try:
                obj = pdf_doc.xref_get_key(xref, "Type")
                if obj and "OCG" in str(obj):
                    # This is an Optional Content Group
                    name_obj = pdf_doc.xref_get_key(xref, "Name")
                    if name_obj:
                        layer_name = str(name_obj).strip('()/')
                        layer_names.add(layer_name)
                        
                        # Try to get visibility state
                        visible = True  # Default assumption
                        layers.append({
                            "name": layer_name,
                            "visible": visible,
                            "locked": False,
                            "xref": xref
                        })
            except:
                continue
                
    except Exception as e:
        logger.debug(f"Alternative layer extraction failed: {str(e)}")
    
    return layers, layer_names

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
