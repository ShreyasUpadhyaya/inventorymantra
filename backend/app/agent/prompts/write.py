"""Write extraction task block and action list: verbatim from docs/PROMPTS.md §7 (v0.8)."""

WRITE_TASK = """\
TASK: The user wants to change data. Choose ONE of the actions in <actions> (or several if the
message clearly asks for several, e.g. "add 3 to 132-2 and 5 to 132-1") and fill their
fields from the message. Only the actions in <actions> exist for this user.
- Copy names exactly as the user wrote them (items, warehouses, vendors, teams, PO numbers),
  in any language; the system finds the records and asks if a name is unclear.
- If the message points to something instead of naming it ("the other", "it", "same item"),
  use earlier turns only if they name it clearly; otherwise leave the field empty.
- Quantities are plain numbers; leave out the unit ("3 pcs" -> 3).
- Dates: write YYYY-MM-DD. Turn words like "tomorrow" or "next Friday" into a date using
  today's date; if the user gave no date, leave it empty.
- Stock coming in against a PO number -> receive_against_po. Stock that arrived with no PO
  -> record_unexpected_receipt. Stock given to a shop floor or team -> record_stock_movement
  with kind "issue". Other additions or removals -> record_stock_movement with kind
  "inbound" or "outbound". Moving between warehouses -> transfer_stock. Asking procurement
  to buy -> raise_stock_request.
- Do not compute totals, prices, tax, new stock levels or PO numbers; the system does that.
  Use a unit price only if the user stated it.
- If a required field is missing (quantity, item, warehouse, vendor, price), do not guess:
  leave it empty; the system asks the user, one question at a time.
- If the message only asks a question, or no action in <actions> fits, set
  none_applicable to true.
- Never say the change is done: the user still has to confirm a preview.
Return {actions: [{action, ...fields}], none_applicable}.\
"""

# One line per action; each role sees only the lines for its permitted actions.
ACTION_LINES: dict[str, str] = {
    "record_stock_movement": "- record_stock_movement: stock in, out, or issued to a team. "
    "Fields: kind (inbound | outbound | issue), item, location, quantity, issued_to (team, "
    "for issue only), note",
    "transfer_stock": "- transfer_stock: move stock between warehouses. Fields: item, "
    "from_location, to_location, quantity, note",
    "receive_against_po": "- receive_against_po: goods arrived against a PO. Fields: po, "
    "item, quantity, location, note",
    "record_unexpected_receipt": "- record_unexpected_receipt: goods arrived with no PO. "
    "Fields: item, quantity, location, vendor_name, note",
    "raise_stock_request": "- raise_stock_request: ask procurement to buy. Fields: item, "
    "quantity, needed_by, note",
    "create_purchase_order": "- create_purchase_order: place a PO. Fields: vendor, lines "
    "(item, quantity, unit_price, tax_percentage), expected_delivery_date, delivery_location",
    "update_po_expected_date": "- update_po_expected_date: change when a PO is expected. "
    "Fields: po, expected_delivery_date",
    "cancel_po": "- cancel_po: cancel a PO. Fields: po, reason",
    "clear_payment_tranche": "- clear_payment_tranche: mark a PO instalment as paid. Fields: "
    "po, stage",
    "set_reorder_policy": "- set_reorder_policy: set the reorder level, quantity and lead time "
    "of an item. Fields: item, min_level, reorder_qty, lead_time_days, safety_stock, "
    "preferred_vendor",
    "create_vendor": "- create_vendor: add a vendor. Fields: name, city, payment_terms",
    "decide_approval": "- decide_approval: approve or reject a PO that is waiting for the Owner. "
    "Fields: po, decision (approve | reject), note",
    "ask_about_approval": "- ask_about_approval: ask the person who requested a waiting PO a "
    "question. Fields: po, message",
    "reply_to_approval": "- reply_to_approval: answer the Owner's question about your waiting "
    "PO. Fields: po, message",
    "create_user": "- create_user: add a person who can sign in. Fields: full_name, email, role "
    "(inventory_manager | procurement_manager | owner)",
    "deactivate_user": "- deactivate_user: stop a person from signing in. Fields: user (email or "
    "name)",
}
