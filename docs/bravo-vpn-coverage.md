# Bravo 8 and VPN coverage

The [question bank](../artifacts/questions/software/bravo-vpn.json) preserves all 104 employee questions across eight groups and adds two routing regressions. The [turn oracles](../artifacts/questions/oracles/software.bravo-vpn.json) check the selected task, answer status, source revision, exact citation quotes and declared limitations. Nine [conversation scenarios](../artifacts/questions/conversation/bravo-vpn.json) cover short follow-ups, scope changes, unassessed document lookup, credential requests and unavailable evidence.

The corpus currently has one directly relevant guide, [RTV/ERP/Bravo-VPN v1.0](../chunks/3ed6866d-f10f-4c3e-82d6-d35fd18eff90/metadata.json), updated on 11 November 2023. It describes company versus external networks, the Windows RitaVO VPN setup and connection indicator, installation and updates for Bravo Desktop, application permissions and the support teams listed at that date. The answer catalog binds claims to that revision. An explicit source gap such as audit logging returns `insufficient_evidence` with an explanation and no claims or citations.

`Connected` identifies the documented VPN connection state. Bravo access and actions depend on separately documented application permissions. The SharePoint route handles access to the installer folder. The bot reports the limits of its guide when asked about a live outage, an account, or whether a specific save or approval succeeded. Contact guidance carries the guide date. Screenshot guidance asks employees to omit credentials, verification codes and irrelevant private data.

For a single narrowly matched question, declared retrieval bindings prioritize eligible references from the guide. Candidate order remains unassessed. The answer path reads exact references separately from ranked retrieval. [Catalog contract](catalog-answering.md#runtime-contract)

## Internal sources needed for phase 2

| Question group | Cases | Evidence to obtain from the IT or ERP owner |
| --- | ---: | --- |
| Access | 15 | Network and site requirements, supported remote locations, mobile networks and application scope |
| Provisioning | 16 | Request channels, approval, required inputs, eligibility, expiry, renewal and revocation |
| Setup | 18 | Approved installer sources, supported devices and clients, setup order and device replacement |
| Authentication | 15 | Account type, reset and lockout procedures, MFA, session limits and company/database selection |
| Troubleshooting | 19 | Mapped error messages, connection and permission diagnostics, performance, exports and printing |
| Interrupted work | 8 | Save and approval verification, prevention of duplicate submissions, session recovery and deadline escalation |
| Security | 7 | Remote access policy, personal devices, account and data handling, incident reporting and audit logging |
| Support | 6 | Current intake channels, required diagnostics, secure screenshot handling, maintenance and after-hours support |

Record the owner, version, applicability and access rules for each received source. Prepare and publish eligible packages through the existing corpus workflow. Bind complete answers only to references sufficient for the requested scope. Keep requests about current account, service and transaction state tied to the documented operational verification path. [Offline indexing and publication](offline-indexing.md#scope-2-package-reading-embedding-and-publication)

## Verification and rollout

Run the public question bank and the catalog and runtime inventory checks described in the [release workflow](business-topics.md#authoring-evaluation-and-release). The independent acceptance set uses four privately authored rephrasings per group, with the same set run on baseline and candidate. A source-fixture run verifies routing and the evidence contract. A read-only check of the target publication and an authenticated Portal staging conversation verify deployed behavior.

In the staging room, ask how to tell when VPN has connected, follow up with a Bravo access failure, ask whether an interrupted save succeeded, and request the guide explicitly. Verify that each reply has the expected scope and sources and that lookup is labelled as unassessed. Confirm that the target publication matches the deployed card references. Deploy the bot with the matching generated artifacts and inventory. [Conversation staging procedure](conversations.md#deletion-and-verification)
