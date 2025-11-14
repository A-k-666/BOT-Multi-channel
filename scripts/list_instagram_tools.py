"""List available Instagram tools from Composio."""

from dotenv import load_dotenv

from composio import Composio


def list_instagram_tools():
    """List all available Instagram tools."""
    load_dotenv()
    
    client = Composio()
    
    try:
        # List all tools
        tools = client.tools.list()
        print("\n=== Instagram-related Tools ===")
        instagram_tools = []
        for tool in tools.items:
            tool_name = str(tool.name).upper() if hasattr(tool, 'name') else str(tool).upper()
            tool_slug = str(tool.slug).upper() if hasattr(tool, 'slug') else str(tool).upper()
            if 'INSTAGRAM' in tool_name or 'INSTAGRAM' in tool_slug:
                instagram_tools.append(tool)
                print(f"\nTool Name: {getattr(tool, 'name', 'N/A')}")
                print(f"Tool Slug: {getattr(tool, 'slug', 'N/A')}")
                print(f"Toolkit: {getattr(tool, 'toolkit', 'N/A')}")
                print("-" * 50)
        
        if not instagram_tools:
            print("No Instagram tools found.")
            print("\nTrying to search for messaging tools...")
            for tool in tools.items:
                tool_name = str(getattr(tool, 'name', '')).upper()
                if 'MESSAGE' in tool_name and ('SEND' in tool_name or 'POST' in tool_name):
                    print(f"\nFound messaging tool: {getattr(tool, 'name', 'N/A')}")
                    print(f"Slug: {getattr(tool, 'slug', 'N/A')}")
                    print(f"Toolkit: {getattr(tool, 'toolkit', 'N/A')}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    list_instagram_tools()

