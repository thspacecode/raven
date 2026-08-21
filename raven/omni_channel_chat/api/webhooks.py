import frappe
from werkzeug.wrappers import Response

from raven.omni_channel_chat.doctype.omni_channel_chat_provider.omni_channel_chat_provider import (
	get_omni_channel_chat_provider,
)
from raven.omni_channel_chat.omni_channel_raven_connector import OmniChannelRavenConnector


def extract_provider_slug() -> str:
	"""Extract the trailing path segment (slug) from the current request URL.

	Strips a trailing slash if present, then returns the last path component.
	Used by webhook handlers to identify which Omni Channel Chat Provider configuration
	should process the incoming request.

	Returns:
		str: The slug portion of the request path.

	Example:
		For a request to `/api/method/raven.omni_channel_chat.api.webhooks.handle/g9ju6k0e8r`,
		this returns `g9ju6k0e8r`.
	"""
	request = frappe.local.request
	return request.path.rstrip("/").rsplit("/", 1)[-1]


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def handle() -> Response:
	"""
	Endpoint:
		/api/method/raven.omni_channel_chat.api.webhooks.handle
	"""
	slug = extract_provider_slug()
	provider = get_omni_channel_chat_provider(slug=slug)
	connector = OmniChannelRavenConnector(provider=provider)

	return provider.handle_frappe_api(callback=connector.handle_inbound)
