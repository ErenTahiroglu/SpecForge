"""Local read-only MCP tool; stdout is reserved for stdio protocol."""
from mcp.server.fastmcp import FastMCP
from .core import audit_documents

mcp = FastMCP("EngineeringAuditor")


@mcp.tool()
def compare_engineering_rows(document_a: str, document_b: str,
                             tolerance: float = 0.01) -> dict:
    """Compare structured TAG | property | value unit rows with cited line evidence.

    Input must be pipe-delimited rows. This tool receives no filenames, so it does not enable CSV grammar: comma-separated
    input is not safely audited here (use the Streamlit app or the API with .csv filenames). Branch on `verdict` and
    `coverage_complete`, not on `status` alone: NO_DIFFERENCES_FOUND never means the audit was complete.
    """
    return audit_documents({"spec": document_a, "bom": document_b}, tolerance)


if __name__ == "__main__":
    mcp.run(transport="stdio")
