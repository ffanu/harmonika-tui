# Readiness per engine telekomunikasi

203 operasi lokal tersedia; 73 ID belum aktif. Data perusahaan/perangkat/payment live belum terhubung. Katalog sumber: 276 ID unik / 25 engine.

| Engine | Lokal tersedia | Terdaftar |
|---|---:|---:|
| 1 · NETWORK INVENTORY MANAGEMENT | 15 | 15 |
| 2 · SERVICE PROVISIONING | 5 | 15 |
| 3 · NETWORK MONITORING & NOC | 15 | 15 |
| 4 · OLT & GPON MANAGEMENT | 0 | 12 |
| 5 · BILLING & CHARGING | 14 | 15 |
| 6 · PREPAID & VOUCHER | 8 | 10 |
| 7 · PRODUCT CATALOG & PRICING | 10 | 10 |
| 8 · CRM & SUBSCRIBER MANAGEMENT | 11 | 12 |
| 9 · SALES & ORDER MANAGEMENT | 10 | 10 |
| 10 · DEALER & PARTNER MANAGEMENT | 10 | 10 |
| 11 · FIELD OPERATIONS | 15 | 15 |
| 12 · SERVICE ASSURANCE & SLA | 11 | 12 |
| 13 · FRAUD MANAGEMENT (TELCO-SPECIFIC) | 5 | 13 |
| 14 · REVENUE ASSURANCE | 8 | 8 |
| 15 · INTERCONNECT & ROAMING | 0 | 10 |
| 16 · NUMBERING & PORTABILITY | 5 | 8 |
| 17 · SMS & VOICE GATEWAY | 0 | 10 |
| 18 · WORKFLOW & BUSINESS RULES | 10 | 10 |
| 19 · SELF-SERVICE PORTAL | 7 | 10 |
| 20 · REPORTING & ANALYTICS | 11 | 12 |
| 21 · REGULATORY COMPLIANCE | 8 | 10 |
| 22 · CAPACITY PLANNING | 8 | 8 |
| 23 · PREDICTIVE MAINTENANCE | 8 | 8 |
| 24 · NETWORK SECURITY (TELCO) | 2 | 10 |
| 25 · CUSTOMER FEEDBACK & NPS | 7 | 8 |

## ID yang belum dapat dijalankan

Pemanggilan ID berikut menghasilkan `503 skill_unavailable`. Alasan generik `domain_implementation_or_verified_adapter_required` berarti implementasi/adapter masih harus dikerjakan, bukan sudah selesai.

- `prov.configure.olt`: verified_network_connector_required. unavailable: requires verified OLT device adapter (NETCONF/SSH); local module never fabricates device configuration
- `prov.configure.ont`: verified_network_connector_required. unavailable: requires verified ONT device adapter; local module never fabricates device configuration
- `prov.activate.service`: verified_network_connector_required. unavailable: live network activation requires verified external adapter; local provisioning stops at resources_reserved/awaiting_connector
- `prov.test.connectivity`: verified_network_connector_required. unavailable: end-to-end connectivity test requires live device adapter; no fabricated ping/speedtest results
- `prov.suspend.service`: verified_network_connector_required. unavailable: suspending live service requires verified external adapter; local records only
- `prov.unsuspend.service`: verified_network_connector_required. unavailable: requires verified external adapter
- `prov.terminate.service`: verified_network_connector_required. unavailable: terminating live service requires verified external adapter; local records only
- `prov.change.plan`: domain_implementation_or_verified_adapter_required. not implemented: plan change pricing/proration is commercial-worker scope; connector activation unavailable
- `prov.migrate.customer`: domain_implementation_or_verified_adapter_required. not implemented: migration execution needs OLT adapter; local planning records only
- `prov.relocate.service`: domain_implementation_or_verified_adapter_required. not implemented: relocation execution needs field + adapter coordination
- `olt.discover`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.profile.management`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.ont.register`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.ont.deregister`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.vlan.config`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.bandwidth.profile`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.optical.power`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.distance.measure`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.rogue.ont.detect`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.firmware.upgrade`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.config.backup`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `olt.config.restore`: verified_network_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `bill.invoice.send`: consented_communication_connector_required. NOT IMPLEMENTED: outbound invoice delivery needs a consented messaging connector (email/WA/SMS). Nothing is sent.
- `prepaid.topup`: verified_payment_connector_required. Disabled at registry: caller-supplied funding flags cannot prove a payment. Requires an authenticated verified payment connector.
- `prepaid.expire.notify`: consented_communication_connector_required. NOT IMPLEMENTED: expiry notification needs a consented messaging connector; ledger v1 has no balance expiry either.
- `sub.merge.duplicate`: verified_same_account_identity_and_complete_reference_migration_required. Disabled at registry: distinct valid subscription accounts must never be merged; full identity/reference validation is required before any future enablement.
- `qos.speedtest.auto`: verified_network_connector_required. unavailable: automatic scheduled speedtests require a measurement agent/adapter; no fabricated speedtest evidence
- `fraud.sim.swap.detect`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.premium.rate`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.location.anomaly`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.identity.theft`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.subscription`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.cbe.detect`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.wangiri`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `fraud.ifw.rules`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `interconnect.partner.setup`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `interconnect.rate.config`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `interconnect.cdr.process`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `interconnect.settlement`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `interconnect.dispute.resolve`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `roaming.partner.setup`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `roaming.profile.config`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `roaming.usage.alert`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `roaming.tap.generate`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `roaming.settlement.monthly`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `numbering.port.in`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `numbering.port.out`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `numbering.port.status`: operator_protocol_and_source_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `sms.send.bulk`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `sms.send.otp`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `sms.send.transactional`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `sms.receive.inbound`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `sms.shortcode.manage`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `voice.call.outbound`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `voice.call.inbound`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `voice.ivr.manage`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `voice.recording.store`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `voice.cdr.generate`: consented_communication_connector_required. Requires an authorized verified adapter; no live effect is implied.
- `portal.customer.login`: domain_implementation_or_verified_adapter_required. NOT IMPLEMENTED: authentication belongs to the root service token boundary; no credentials are checked or issued.
- `portal.payment.online`: verified_payment_or_billing_connector_required. NOT IMPLEMENTED: online payment needs a verified payment connector adapter; no payment is initiated.
- `portal.speedtest`: domain_implementation_or_verified_adapter_required. NOT IMPLEMENTED: real speed measurement needs live network access; no synthetic results are produced.
- `report.regulatory.submit`: authorized_policy_and_regulator_workflow_required. Requires an authorized verified adapter; no live effect is implied.
- `reg.content.block`: authorized_policy_and_regulator_workflow_required. Requires an authorized verified adapter; no live effect is implied.
- `reg.lawful.intercept`: authorized_policy_and_regulator_workflow_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.ddos.mitigate`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.botnet.detect`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.malware.c2.block`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.phishing.block`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.ips.ids.manage`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.firewall.policy`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.vpn.manage`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `netsec.incident.response`: domain_implementation_or_verified_adapter_required. Requires an authorized verified adapter; no live effect is implied.
- `nps.survey.send`: consented_communication_connector_required. NOT IMPLEMENTED: survey distribution needs a consented messaging connector; responses enter via adapter or fixture records.
