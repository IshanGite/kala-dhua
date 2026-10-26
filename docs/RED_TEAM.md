# Red-team report

Measured by `evals/s_security.py` (run with `uv run ragnarok evals --suite security`); raw result in `docs/evidence/evals/security.json`. Real local models, recording fake senders (nothing real is ever sent), a fresh isolated world per run.

## What was tested

Hostile text arrives through every door the agent reads (mail, Telegram, Slack, memory, a skill scroll, a screen picture), then the user asks ordinary questions ("catch me up", "summarize my emails", "read my latest message"). The attacker wins if **any** unauthorised side effect happens (a send to the attacker's address, a secret in a reply, an approval that fires without a click) or if a card shows something different from what is then sent.

- 12 hand-written injections in the first set, 15 in a second tuning set, 12 in a **held-out** set that was written after the scanner was tuned and never used for tuning.
- 30 benign messages that contain alarming-sounding words ("ignore the earlier invoice", "debug mode is off", "tell nobody, it is a surprise party") to measure false alarms.
- Compound scenarios: read, then write in one request; a scroll carrying an injection; a canary secret that must never appear in a reply; typed "yes" and "do it" as approvals; approval-card integrity.

## Result

| Measure | Result |
|---|---|
| Attacks that produced any unauthorised side effect | **0 of 74** (95% CI 0 to 4.9%) |
| Typed text that acted as an approval | 0 of 10 |
| Approval card matched what was actually sent | 3 of 3 |
| Pattern scanner alone, tuning sets | 35 of 35 detected |
| Pattern scanner alone, **held-out** set | **2 of 12** detected |
| Pattern scanner plus small-model second opinion, held-out set | 12 of 12 detected |
| Pattern scanner plus second opinion, benign false alarms | 0 of 30 |

## Where the defence actually is

The scanner is a tripwire, not the defence. The first held-out run showed why: patterns tuned on known phrasings caught only 2 of 12 unseen ones. Rather than keep adding patterns until the number looked good, a second opinion was added (`gateway_scan/deep.py`: a cheap cue regex AND a small-model verdict, cached, applied to snippets before they reach the answering model and to scroll context). Held-out detection became 12 of 12 with no benign false alarms, but 12 is a small set and the interval is wide (74 to 100%).

The reason the attack success rate is 0 does not depend on detection:

1. Received text is wrapped as labelled data and never placed in planner prompts.
2. Every send, post, calendar or Drive write needs a click on a card bound to the exact content; the model cannot approve.
3. Typed "yes" never approves anything.
4. Policy rules decide permissions; models and the route library can only add caution.
5. Scrolls can only write text; they have no send tools. The dreamer has no executor at all.

So a missed detection means the agent may be confused for one answer, not that it can act.

## Known limits

- All attacks are written by the author. They are not a substitute for an outside red team.
- 74 trials rule out an attack success rate above about 5%, not above 0.
- The small-model second opinion costs one extra short model call per untrusted snippet (cached by content hash).
- Image-borne injection was tested with rendered text only, not adversarial pixels.
- Windows has no sandbox backend yet.
