# Investigation Methodology (Agent Rules)

Source: Brendan Gregg, *Systems Performance*, 2nd ed., 2020 ("SP"). Section numbers follow the book.
Tag `[ops]` marks general Linux practice that is not from SP.
All text is paraphrased. Follow these rules in every investigation.

## 1. Core rules (MUST)

1. Write a problem statement before you run any tool. (SP §2.5.5)
2. State one hypothesis and one prediction before each command. (SP §2.5.6)
3. Use observation tools first. Do not run benchmarks or change settings in production. (SP §1.7)
4. For each resource, check errors first, then saturation, then utilization. (SP §2.5.9)
5. Quantify each finding. Express it as latency, or as % of request time. (SP §1.5.4, §1.6)
6. Record each metric you could not get as a "known-unknown". Report these gaps. (SP §2.3.15, §2.5.9)
7. Check limits from cgroups and cloud quotas before physical limits. Limits are hit first. (SP §11.3.4.4)
8. Stop when your finding explains most of the problem. Continue if it explains only a small part. (SP §2.3.6)
9. Expect more than one cause. Do not stop at the first anomaly. (SP §1.5.3, §2.5.9)
10. Use only read-only commands. Always give commands a count or timeout so that they end. `[ops]`

## 2. Problem statement (ask first) — SP §2.5.5

1. What makes you think there is a performance problem?
2. Did this system ever perform well?
3. What changed recently? Software, hardware, config, or load?
4. Can you express the problem as latency or run time?
5. Does the problem affect other users or apps, or only one?
6. What is the environment? Software, hardware, versions, configuration?
These answers often point to the cause before you log in. Record them in the report.

## 3. Scientific method loop — SP §2.5.6, §2.5.7

Loop: **Question → Hypothesis → Prediction → Test → Analysis**.
- The question is the problem statement.
- A test is observational (measure) or experimental (change one thing).
- A negative result is useful. It removes a component from the search.
- If an experiment fails, undo it before you test the next hypothesis.
- Change only one factor at a time.
- Move from hypothesis to data quickly. Discard bad hypotheses early. (diagnosis cycle, SP §2.5.7)

## 4. USE method (resources) — SP §2.5.9

For every resource, check **U**tilization, **S**aturation, and **E**rrors.
- Utilization: % of time busy in an interval. For capacity resources (memory, disk space), % of capacity used.
- Saturation: queued work that the resource cannot service yet. Also called pressure.
- Errors: count of error events.
Procedure:
1. List all resources: CPUs, memory, network interfaces, storage devices, controllers, interconnects.
2. Add software resources: file descriptors, process/thread slots, thread pools, locks.
3. Add every resource control: cgroup CPU, memory, I/O, and cloud quotas.
4. For each resource, check E, then S, then U. Use `use-checklist-linux.md`.
5. Note each metric you cannot read. These are known-unknowns.
Interpretation:
- 100% utilization usually means a bottleneck. Confirm it with saturation.
- Above about 60% utilization, queueing delay grows. Long intervals can hide bursts at 100%.
- Any non-zero saturation can be a problem.
- Non-zero errors need investigation, especially if they increase during the problem.
- Low U, zero S, zero E: the resource is probably not the cause. Eliminate it and move on.

## 5. RED method (services) — SP §2.5.10

For every service, check request **R**ate, **E**rrors, and **D**uration (use percentiles, not only averages).
- USE shows machine health. RED shows user health. Use both.
- Rate steady and duration up: suspect the service or its dependencies (architecture).
- Rate up and duration up: suspect load. Do workload characterization next.
- For microservices, approximate saturation as p99 latency minus average latency. (SP §2.5.9)

## 6. Workload characterization — SP §2.5.11

Measure the input, not the result. Answer:
- **Who** causes the load? PID, user, remote IP, container?
- **Why** is the load called? Code path, stack, caller?
- **What** are its properties? IOPS, throughput, read/write mix, size, variance?
- **How** does it change over time? Daily or hourly pattern?
Check these even when you think you know the answers. A DoS, a runaway loop, or a backup at peak can be the cause.
The best fix often removes unnecessary work.

## 7. Drill-down analysis — SP §2.5.12

1. Monitoring: find the problem service or host from fleet metrics.
2. Identification: narrow it to one resource with per-host tools (USE, 60-second list).
3. Analysis: inspect that resource deeply (profiles, traces, logs) to find the root cause.
- Discard areas that look normal. Dig into areas that look abnormal.
- Use "Five Whys": ask "why?" after each answer, at least five times.

## 8. Latency analysis — SP §2.5.13, §8.5.2

1. Measure the full operation time (for example, request latency).
2. Split it into two parts. Example: on-CPU time vs off-CPU (waiting) time.
3. Find the larger part. Split that part again.
4. Repeat until you find and quantify the origin.
Example chain: query → off-CPU → file system → disk I/O → queueing vs service time.
Express cost as % of request time. If a component adds only 1%, look elsewhere. (SP §8.5.2)

## 9. Linux 60-second checklist — SP §1.10.1, Table 1.1

Run in this order. Add a count so each command ends (for example `1 5`). `[ops]`

| # | Command | What it shows |
|---|---------|---------------|
| 1 | `uptime` | Load averages 1/5/15 min. Load rising or falling? Linux load includes D-state (disk) tasks. |
| 2 | `dmesg -T \| tail` | Kernel errors: OOM kills, I/O errors, TCP drops, hardware errors. |
| 3 | `vmstat -SM 1 5` | Run queue `r`, blocked `b`, free memory, swap `si/so`, CPU `us sy id wa st`. |
| 4 | `mpstat -P ALL 1 5` | Per-CPU balance. One hot CPU means a single-thread limit. Check `%steal`. |
| 5 | `pidstat 1 5` | CPU per process, user vs system time. Finds unexpected consumers. |
| 6 | `iostat -sxz 1 5` | Per-disk IOPS, throughput, `await`, `aqu-sz`, `%util`. |
| 7 | `free -m` | Memory use. Includes file system cache, which is reclaimable. |
| 8 | `sar -n DEV 1 5` | Interface packets and throughput. Compare with link speed. |
| 9 | `sar -n TCP,ETCP 1 5` | TCP connection rates and retransmits. |
| 10 | `top -b -n 1` | Overview. Use batch mode for agents. `[ops]` |

Then add: `cat /proc/pressure/{cpu,memory,io}` for PSI saturation (Linux 4.20+). (SP §6.6.1)
Do not spend more than a few minutes on load averages. Move to detailed metrics. (SP §6.6.1)

## 10. Anti-methods (MUST NOT)

- **Streetlight (SP §2.5.1):** Do not run a tool only because it is familiar or available. Choose tools from the hypothesis and the USE list.
- **Random change (SP §2.5.2):** Do not change tunables to "see if it helps". Never propose a change you cannot explain with data.
- **Blame someone else (SP §2.5.3):** Do not blame the network, database, or another team without evidence. Show the command, the output, and your interpretation.
- **Tools method only (SP §2.5.8):** Do not limit the search to what known tools show. Iterate over resources, not tools.
- **Copied tunables (SP §2.3.7):** Do not apply internet tuning advice. Advice is valid only for one point in time and one workload.

## 11. Symptom vs cause; correlation vs causation

- A busy resource can be a symptom. Example: disks were busy. The cause was another app's memory growth. It shrank the page cache and caused more disk reads. (SP §1.11.1)
- A slow request at the tail of a queue can be caused by the requests before it. (SP §2.5.15)
- One failed component can cause problems in others (cascade). Find the first failure. (SP §1.5.2)
- Several normal events can combine to cause one problem. (SP §1.5.3)
- Fixing one bottleneck can move it elsewhere. Re-check after each fix. (SP §1.5.2)
- A start time that matches a deploy or config change is a strong lead. It is not proof. Test it. (SP §1.7.1)
- Time correlation is not causation. Confirm cause with a test, or with a latency breakdown on the critical path. `[ops]`
- High I/O latency hurts only if the app waits on it. Async I/O may not affect the request. (SP §9.3.9)
- People blame what they cannot observe. Get data before you accept a "noisy neighbor" claim. (SP §11.3.4.3)

## 12. Load vs architecture — SP §2.3.8

- Architecture problem: work queues while resources are idle. Examples: single thread at 100% on one CPU, one lock.
- Load problem: all resources busy and work still queues. Add capacity or reduce load.
- Request rate (RED) tells you which case applies.

## 13. Statistics traps — SP §2.8, §2.9

- An average can hide a bimodal distribution. Ask for the distribution or percentiles. (SP §2.8.5)
- Outliers matter. Look at p99 and max, not only mean. (SP §2.8.6)
- 1-minute or 5-minute averages hide short bursts. Prefer 1-second samples. (SP §2.5.9, §6.5.6)
- Compare with a baseline: last week, same hour, or a healthy peer host. (SP §2.5.16, §2.9)
- With no baseline, compare current values with summary-since-boot values. (SP §2.9.3)
- Metrics can be wrong. Cross-check one source with another. (SP §2.3.10)

## 14. Static checks (quick wins) — SP §2.5.17

Check configuration while you wait for data:
- Interface negotiated at a lower speed (1 Gb/s instead of 10 Gb/s).
- Failed disk in a RAID pool, or a RAID rebuild in progress.
- File system nearly full.
- Debug mode left on in the app.
- IP forwarding enabled by mistake.
- Dependency (auth, DNS, DB) served from a remote data center.
- Old OS, app, or firmware version.

## 15. Report format (MUST)

1. Problem statement and impact (latency or error rate).
2. Timeline: start time, and changes near that time.
3. Evidence: command, key output values, interpretation.
4. Root cause, with the % of the problem that it explains.
5. Eliminated hypotheses, and the data that eliminated them.
6. Known-unknowns: metrics that were not available.
7. Recommended fix. Mark it as a hypothesis until a test confirms it.
