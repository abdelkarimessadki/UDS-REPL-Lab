# UDS-REPL-Lab
**UDS-REPL Lab** is a lightweight, laptop-only playground for learning and testing UDS (ISO 14229) diagnostics. It gives you:

- An **interactive Python REPL** where you type raw UDS requests (e.g., 10 03, 22 F1 87) and see live ECU responses.

- A built-in **mini ECU stub** that emulates common services over **ISO-TP Single Frame** using python-can’s virtual bus (no hardware needed).

- Supported SIDs include **0x10** (sessions), **0x11** (reset), **0x22** (DIDs like F187/F188…), **0x27** (seed/key demo), **0x19** (DTC report), **0x31** (routine FF00), **0x3E** (tester present), **0x85** (control DTC).

- **Configurable IDs/timeouts**, interim “response pending” handling, and clean explanations of positive/negative responses.

It’s ideal for **training, prototyping, and demos**, and is designed to be easily extended to multi-frame ISO-TP later (VIN, downloads, etc.).
