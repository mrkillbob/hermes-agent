# HRL-7 Scout System Design

Four bounded scouts accept normalized facts from public/permitted sources. They do not browse,
contact customers, or infer eligibility from prose alone. Every candidate retains source URL,
permission basis, collection time, content digest, fact code, and bounded observed value.

Eligibility is deterministic by scout kind. Business problems need an objective diagnostic fact;
data opportunities need fragmentation, repeated updates, economic utility, and historical value;
alerts need an authoritative public/API source and time-sensitive monetary value; digital products
need a narrow utility type plus demand evidence. Generic AI-art products are explicitly rejected.

A private root-contained SQLite store saves both eligible and rejected candidates with every raw
fact and the categorical verdict. Run and evidence bounds prevent unbounded collection.
