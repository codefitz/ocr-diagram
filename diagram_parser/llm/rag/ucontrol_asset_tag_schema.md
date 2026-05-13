# uControl Infrastructure Model Guidance

Extract named infrastructure nodes and their relationships so the output can be transformed into a uControl model-create request body.

Only extract nodes with an identifiable visible name. Ignore unnamed icons, unlabeled boundaries, decorative symbols, and inferred assets that are not visible in the diagram.

Separate asset names from role/type/description text:

- `Web Servers BDHW8KW3 BDHW8KW4 BDHW8KW5` means three `host` nodes named `BDHW8KW3`, `BDHW8KW4`, and `BDHW8KW5`.
- `VM BDHW8KW6 Data Store Server` means one `host` node named `BDHW8KW6`.
- `DB clm-ais-007438` or `Database clm-ais-007438` usually means one database server `host` named `clm-ais-007438`, not a database CI.
- `VM`, `Web Servers`, `DB`, `Database`, `Data Store Server`, `BACKUP`, CPU, RAM, OS, and hardware-model text are descriptions or specifications, not host names.
- A router, switch, firewall, or load balancer can be named by its visible device label, such as `F5 Switch`; trailing OS or model text should not become the name.

Use these node types:

- `host`: hosts, servers, VMs, EC2 instances, bastions, compute nodes
- `router_switch`: routers, switches, gateways, load balancers, network devices
- `firewall`: firewalls, WAFs, security appliances
- `software`: applications, services, APIs, runtimes, frontend/backend components
- `database`: named database services or technologies such as RDS, PostgreSQL, MySQL, Redis, MongoDB; do not use for a server/host merely labelled DB
- `unknown`: only when a named infrastructure node is visible but the type is not clear

The downstream uControl mapping uses these definitions:

- host: `Host`
- router_switch: `NetworkDevice`
- firewall: `Firewall`
- software: `SoftwareInstance`
- database: `Database`
- unknown: `GenericElement`

Preserve visible relationships or connections between named nodes. Include protocol and port only when the diagram explicitly shows them.
