# Problem Statement

## MediRoute — Community Clinic Triage Assistant

Rural community clinics in low-connectivity regions run walk-in queues with no
triage. A single nurse sees 60–90 patients a day and decides ordering by
intuition. Genuinely urgent cases (sepsis onset, cardiac events, obstetric
emergencies) routinely wait behind minor complaints, and the clinic has no
record of why anyone was prioritised.

Build an AI-powered triage assistant that a nurse can use on a low-end tablet.
It should take a short intake description plus basic vitals, produce an urgency
score with a plain-language explanation the nurse can accept or override, and
maintain an auditable queue. It must degrade gracefully when the network drops.

The system must be usable by someone with no technical training, in under
30 seconds per patient, and must never present itself as a diagnosis.
