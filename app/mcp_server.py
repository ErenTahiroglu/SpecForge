"""Local read-only MCP tool; stdout is reserved for stdio protocol."""
from mcp.server.fastmcp import FastMCP
from .core import audit_documents

mcp = FastMCP("EngineeringAuditor")


@mcp.tool()
def compare_engineering_rows(document_a: str, document_b: str,
                             tolerance: float = 0.01) -> dict:
    """Compare structured TAG | property | value unit rows with cited line evidence."""
    return audit_documents({"spec": document_a, "bom": document_b}, tolerance)


if __name__ == "__main__":
    mcp.run(transport="stdio")
