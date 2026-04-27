# uControl Asset Tag JSON Body Guidance

When diagram capture output is transformed for uControl asset/tag creation, each detected asset must be representable as this JSON body shape:

```json
{
  "record_identifier": null,
  "name": "Asset name from the diagram",
  "short_name": null,
  "type": null,
  "product_version": null,
  "instance": null,
  "uControlID": null,
  "environment": null,
  "version": null,
  "description": "Asset name from the diagram",
  "application_id": null,
  "guid": "stable-base64-identity",
  "datasource_name": null,
  "datasource_key": null,
  "datasource": "UCONTROL",
  "atrium_key": null,
  "bmc_key": null,
  "servicenow_key": null,
  "kind": "BusinessApplicationInstance",
  "deleted_date": "",
  "deleted_status": false,
  "merge_status": false
}
```

Do not create the HTTP API call. Output JSON only.

`record_identifier` is the unique ID created by uControl, so do not invent it during diagram extraction.

Use BMC Discovery-style node kinds for `kind`. Prefer these mappings when the diagram does not explicitly identify a more precise kind:

- application: `BusinessApplicationInstance`
- database: `Database`
- server or runtime component: `SoftwareInstance`
- external network, internet, or third-party endpoint: `ExternalElement`
- zone, boundary, or grouping: `GenericElement`
- unknown: `GenericElement`

Relationships are not documented yet. Preserve extracted topology edges and include a directional primary-key/foreign-key identity so downstream mapping has a stable source and target identity.
