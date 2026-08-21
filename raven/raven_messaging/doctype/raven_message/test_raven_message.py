# Copyright (c) 2023, The Commit Company and contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestRavenMessage(FrappeTestCase):
	def test_omni_channel_push_is_skipped_for_regular_channels(self):
		message = frappe.new_doc("Raven Message")
		message.channel_id = "regular-channel"

		with (
			patch(
				"frappe.db.get_value",
				return_value=frappe._dict(
					is_customer=0,
					omni_channel_chat_provider=None,
				),
			),
			patch(
				"raven.omni_channel_chat.omni_channel_raven_connector."
				"OmniChannelRavenConnector.get_provider_from_channel"
			) as get_provider,
		):
			message.push_message_to_omni_channel_chat_provider()

		get_provider.assert_not_called()
