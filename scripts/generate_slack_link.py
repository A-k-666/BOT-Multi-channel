import argparse
import uuid

from dotenv import load_dotenv

from composio import Composio
from composio_langchain import LangchainProvider


def generate_link(org_id: str, auth_config_id: str) -> str:
    client = Composio(provider=LangchainProvider())
    connection = client.connected_accounts.link(
        user_id=org_id,
        auth_config_id=auth_config_id,
    )
    return connection.redirect_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Composio Slack OAuth link.")
    parser.add_argument("--org-id", default=None, help="Org/workspace ID to use as user_id.")
    parser.add_argument(
        "--auth-config-id",
        default="ac_Bzmda-wXX2YF",
        help="Slack auth config ID (default set to existing config).",
    )
    args = parser.parse_args()

    load_dotenv()

    org_id = args.org_id or f"org_{uuid.uuid4().hex[:8]}"
    url = generate_link(org_id=org_id, auth_config_id=args.auth_config_id)

    print("Org ID:", org_id)
    print("Auth Config:", args.auth_config_id)
    print("Link:", url)


if __name__ == "__main__":
    main()

