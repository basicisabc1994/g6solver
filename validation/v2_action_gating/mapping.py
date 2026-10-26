# SPDX-License-Identifier: Apache-2.0
"""Study V2: the frozen mapping from an AgentDojo tool to the facts a tool registry would declare about it.

Written from the tools' names, parameters and descriptions only (AgentDojo 0.1.35, benchmark v1.2.1), before any
task's ground truth was extracted or read, and frozen with the study (``docs/validation/REGISTRY.md``). It is the
G6-side adapter, so it is published whole.

Each tool gets one class and the facts the safe-actions bundle accepts from a person:

- ``reversibility``: whether the effect can be undone (a sent message or payment cannot; a booking or a share can be
  withdrawn, but not before someone saw or held it, so ``partially_reversible``);
- ``reach``: who the effect reaches (``none`` for a read, ``account`` for the user's own data, ``organization`` for a
  shared workspace, ``external_party`` for someone outside, ``public`` for the open web);
- ``external``: whether the call sends anything outside the user's own systems;
- ``backend``: the system it acts on.

Classes: ``read`` (returns data and changes nothing a person would notice), ``write_local`` (changes the user's
own data and nothing leaves), ``delete``, ``outward`` (sends something to another person or the web), ``money``,
``booking`` and ``access`` (accounts, passwords, memberships). ``target`` names the argument that says what the call
acts on.
"""
from __future__ import annotations

READ = {"reversibility": "reversible", "reach": "none", "external": False}
TOOLS: dict[str, dict] = {
    # workspace: e-mail
    "send_email": {"class": "outward", "reversibility": "irreversible", "reach": "external_party", "external": True,
                   "backend": "email", "target": "recipients"},
    "delete_email": {"class": "delete", "reversibility": "irreversible", "reach": "account", "external": False,
                     "backend": "email", "target": "email_id"},
    "get_unread_emails": {"class": "read", **READ, "backend": "email", "target": ""},
    "get_sent_emails": {"class": "read", **READ, "backend": "email", "target": ""},
    "get_received_emails": {"class": "read", **READ, "backend": "email", "target": ""},
    "get_draft_emails": {"class": "read", **READ, "backend": "email", "target": ""},
    "search_emails": {"class": "read", **READ, "backend": "email", "target": "query"},
    "search_contacts_by_name": {"class": "read", **READ, "backend": "email", "target": "query"},
    "search_contacts_by_email": {"class": "read", **READ, "backend": "email", "target": "query"},
    "get_current_day": {"class": "read", **READ, "backend": "calendar", "target": ""},
    # workspace and travel: calendar (creating, rescheduling and adding participants e-mail the participants)
    "search_calendar_events": {"class": "read", **READ, "backend": "calendar", "target": "query"},
    "get_day_calendar_events": {"class": "read", **READ, "backend": "calendar", "target": "day"},
    "create_calendar_event": {"class": "outward", "reversibility": "partially_reversible", "reach": "external_party",
                              "external": True, "backend": "calendar", "target": "participants"},
    "cancel_calendar_event": {"class": "delete", "reversibility": "partially_reversible", "reach": "account",
                              "external": False, "backend": "calendar", "target": "event_id"},
    "reschedule_calendar_event": {"class": "outward", "reversibility": "partially_reversible",
                                  "reach": "external_party", "external": True, "backend": "calendar",
                                  "target": "event_id"},
    "add_calendar_event_participants": {"class": "outward", "reversibility": "partially_reversible",
                                        "reach": "external_party", "external": True, "backend": "calendar",
                                        "target": "participants"},
    # workspace: cloud drive
    "append_to_file": {"class": "write_local", "reversibility": "partially_reversible", "reach": "account",
                       "external": False, "backend": "drive", "target": "file_id"},
    "search_files_by_filename": {"class": "read", **READ, "backend": "drive", "target": "filename"},
    "create_file": {"class": "write_local", "reversibility": "reversible", "reach": "account", "external": False,
                    "backend": "drive", "target": "filename"},
    "delete_file": {"class": "delete", "reversibility": "irreversible", "reach": "account", "external": False,
                    "backend": "drive", "target": "file_id"},
    "get_file_by_id": {"class": "read", **READ, "backend": "drive", "target": "file_id"},
    "list_files": {"class": "read", **READ, "backend": "drive", "target": ""},
    "share_file": {"class": "outward", "reversibility": "partially_reversible", "reach": "external_party",
                   "external": True, "backend": "drive", "target": "email"},
    "search_files": {"class": "read", **READ, "backend": "drive", "target": "query"},
    # travel: information
    **{name: {"class": "read", **READ, "backend": "travel", "target": target} for name, target in (
        ("get_user_information", ""), ("get_all_hotels_in_city", "city"), ("get_hotels_prices", "hotel_names"),
        ("get_rating_reviews_for_hotels", "hotel_names"), ("get_hotels_address", "hotel_name"),
        ("get_all_restaurants_in_city", "city"), ("get_cuisine_type_for_restaurants", "restaurant_names"),
        ("get_restaurants_address", "restaurant_names"), ("get_rating_reviews_for_restaurants", "restaurant_names"),
        ("get_dietary_restrictions_for_all_restaurants", "restaurant_names"),
        ("get_contact_information_for_restaurants", "restaurant_names"),
        ("get_price_for_restaurants", "restaurant_names"), ("check_restaurant_opening_hours", "restaurant_names"),
        ("get_all_car_rental_companies_in_city", "city"), ("get_car_types_available", "company_name"),
        ("get_rating_reviews_for_car_rental", "company_name"), ("get_car_fuel_options", "company_name"),
        ("get_car_rental_address", "company_name"), ("get_car_price_per_day", "company_name"),
        ("get_flight_information", "arrival_city"))},
    # travel: bookings
    "reserve_hotel": {"class": "booking", "reversibility": "partially_reversible", "reach": "external_party",
                      "external": True, "backend": "booking", "target": "hotel"},
    "reserve_car_rental": {"class": "booking", "reversibility": "partially_reversible", "reach": "external_party",
                           "external": True, "backend": "booking", "target": "company"},
    "reserve_restaurant": {"class": "booking", "reversibility": "partially_reversible", "reach": "external_party",
                           "external": True, "backend": "booking", "target": "restaurant"},
    # banking
    "get_iban": {"class": "read", **READ, "backend": "bank", "target": ""},
    "send_money": {"class": "money", "reversibility": "irreversible", "reach": "external_party", "external": True,
                   "backend": "bank", "target": "recipient"},
    "schedule_transaction": {"class": "money", "reversibility": "partially_reversible", "reach": "external_party",
                             "external": True, "backend": "bank", "target": "recipient"},
    "update_scheduled_transaction": {"class": "money", "reversibility": "partially_reversible",
                                     "reach": "external_party", "external": True, "backend": "bank",
                                     "target": "recipient"},
    "get_balance": {"class": "read", **READ, "backend": "bank", "target": ""},
    "get_most_recent_transactions": {"class": "read", **READ, "backend": "bank", "target": ""},
    "get_scheduled_transactions": {"class": "read", **READ, "backend": "bank", "target": ""},
    "read_file": {"class": "read", **READ, "backend": "bank", "target": "file_path"},
    "get_user_info": {"class": "read", **READ, "backend": "bank", "target": ""},
    "update_password": {"class": "access", "reversibility": "partially_reversible", "reach": "account",
                        "external": False, "backend": "bank", "target": ""},
    "update_user_info": {"class": "write_local", "reversibility": "reversible", "reach": "account", "external": False,
                         "backend": "bank", "target": ""},
    # slack
    "get_channels": {"class": "read", **READ, "backend": "slack", "target": ""},
    "add_user_to_channel": {"class": "access", "reversibility": "reversible", "reach": "organization",
                            "external": False, "backend": "slack", "target": "user"},
    "read_channel_messages": {"class": "read", **READ, "backend": "slack", "target": "channel"},
    "read_inbox": {"class": "read", **READ, "backend": "slack", "target": "user"},
    "send_direct_message": {"class": "outward", "reversibility": "irreversible", "reach": "organization",
                            "external": False, "backend": "slack", "target": "recipient"},
    "send_channel_message": {"class": "outward", "reversibility": "irreversible", "reach": "organization",
                             "external": False, "backend": "slack", "target": "channel"},
    "get_users_in_channel": {"class": "read", **READ, "backend": "slack", "target": "channel"},
    "invite_user_to_slack": {"class": "access", "reversibility": "partially_reversible", "reach": "external_party",
                             "external": True, "backend": "slack", "target": "user_email"},
    "remove_user_from_slack": {"class": "access", "reversibility": "partially_reversible", "reach": "organization",
                               "external": False, "backend": "slack", "target": "user"},
    # a GET reaches the open web (a URL can carry data out), so it is a read with public reach
    "get_webpage": {"class": "read", "reversibility": "reversible", "reach": "public", "external": True,
                    "backend": "web", "target": "url"},
    "post_webpage": {"class": "outward", "reversibility": "irreversible", "reach": "public", "external": True,
                     "backend": "web", "target": "url"},
}
#: The allow-list baseline's rule: a tool whose name starts with a read verb runs; everything else asks a person.
READ_VERBS = ("get_", "search_", "list_", "read_", "check_")
