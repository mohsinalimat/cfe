import base64
import datetime
import json
import os
import traceback

import frappe
import jwt
import requests
from requests.auth import HTTPBasicAuth


def validate():
	"""
	Additional validation to execute along with frappe request
	"""
	authorization_header = frappe.get_request_header("Authorization", str()).split(" ")
	if len(authorization_header) == 2:
		token = authorization_header[1]
		if frappe.get_conf().get("castlecraft_auth_introspect_bearer_enabled"):
			validate_bearer_with_introspection(token)


def validate_bearer_with_introspection(token):
	"""
	Validates access_token by using introspection endpoint
	Caches the token upto expiry for reuse
	"""
	is_valid = False
	email = None

	cached_token = frappe.cache().get_value(f"cc_bearer|{token}")
	now = datetime.datetime.now()
	form_dict = frappe.local.form_dict
	token_response = {}

	try:
		if cached_token:
			token_json = json.loads(cached_token)
			exp = token_json.get("exp")
			email = frappe.get_value("User", token_json.get("email"), "email")
			if exp:
				exp = datetime.datetime.fromtimestamp(int(token_json.get("exp")))
			else:
				exp = now

			if now < exp and email:
				token_response = token_json
				is_valid = True
			else:
				frappe.cache().delete_key(f"cc_bearer|{token}")

		else:
			client_id = frappe.get_conf().get("castlecraft_client_id")
			client_secret = frappe.get_conf().get("castlecraft_client_secret")
			introspect_url = frappe.get_conf().get("castlecraft_introspect_url")
			introspect_token_key = frappe.get_conf().get(
				"castlecraft_introspect_token_key", "token"
			)
			auth_header_enabled = frappe.get_conf().get("castlecraft_auth_header_enabled")
			auth = None
			if not introspect_url:
				return

			if auth_header_enabled and client_id and client_secret:
				auth = HTTPBasicAuth(client_id, client_secret)

			data = {}
			data[introspect_token_key] = token
			r = requests.post(
				introspect_url,
				data=data,
				auth=auth,
				headers={"Content-Type": "application/x-www-form-urlencoded"},
			)
			token_response = r.json()
			exp = token_response.get("exp")

			if exp:
				exp = datetime.datetime.fromtimestamp(int(token_response.get("exp")))
			else:
				exp = now + datetime.timedelta(0, int(token_response.get("expires_in")) or 0)

			if now < exp:
				email = frappe.get_value("User", token_response.get("email"), "email")
				if email and token_response.get("email"):
					frappe.cache().set_value(
						f"cc_bearer|{token}", json.dumps(token_response), expires_in_sec=exp - now,
					)
					is_valid = True

		if frappe.get_conf().get(
			"castlecraft_create_user_on_auth_enabled"
		) and not frappe.db.exists("User", email):
			user = create_and_save_user(token_response)
			frappe.cache().set_value(
				f"cc_bearer|{token}", json.dumps(token_response), expires_in_sec=exp - now,
			)
			is_valid = True

		if is_valid:
			frappe.set_user(email)
			frappe.local.form_dict = form_dict

	except:
		frappe.log_error(traceback.format_exc(), "castlecraft_bearer_auth_failed")


def create_and_save_user(body):
	"""
	Create new User and save based on response
	"""
	user = frappe.new_doc("User")
	user.email = body.get("email")
	user.first_name = body.get("name")
	user.full_name = body.get("name")
	if body.get("phone_number_verified"):
		user.phone = body.get("phone_number")

	user.flags.ignore_permissions = 1
	user.flags.no_welcome_mail = True
	user.save()
	frappe.db.commit()

	return user
