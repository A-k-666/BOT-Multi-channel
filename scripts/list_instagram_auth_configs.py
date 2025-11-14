"""List available Instagram auth configs from Composio."""

from dotenv import load_dotenv

from composio import Composio


def list_auth_configs():
    """List all available auth configs for Instagram toolkit."""
    load_dotenv()
    
    client = Composio()
    
    # List all auth configs
    try:
        auth_configs = client.auth_configs.list()
        print("\n=== All Auth Configs ===")
        for config in auth_configs.items:
            # Check if it's Instagram related
            toolkit_slug = getattr(config, 'toolkit_slug', None) or getattr(config, 'toolkit', None)
            if toolkit_slug and 'INSTAGRAM' in str(toolkit_slug).upper():
                print(f"\nAuth Config ID: {config.id}")
                print(f"Toolkit: {toolkit_slug}")
                print(f"Name: {getattr(config, 'name', 'N/A')}")
                print(f"Created: {getattr(config, 'created_at', 'N/A')}")
                print("-" * 50)
    except Exception as e:
        print(f"Error listing auth configs: {e}")
        print("\nTrying alternative method...")
        
        # Alternative: Try to get Instagram toolkit info
        try:
            toolkits = client.toolkits.list()
            for toolkit in toolkits.items:
                if 'INSTAGRAM' in str(toolkit.slug).upper():
                    print(f"\nFound Instagram Toolkit: {toolkit.slug}")
                    print(f"Toolkit ID: {getattr(toolkit, 'id', 'N/A')}")
        except Exception as e2:
            print(f"Alternative method also failed: {e2}")


if __name__ == "__main__":
    list_auth_configs()

