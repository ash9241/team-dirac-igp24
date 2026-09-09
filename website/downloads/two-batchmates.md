# Two Batchmates Walk Into a Maths Competition

A founder building quantum sensors, a future category theorist, and a month spent learning what AI could help us find.

By Aishwarya Das. Research with Durgesh Kumar.
September 8, 2026.

The competition asked us to work backwards: choose how an equation’s roots should behave, then find an equation whose roots behave that way.

Most of us meet a polynomial as something to solve. Take x² − 2 = 0. Its two roots are √2 and −√2. You can exchange them without changing their sum, their product, or any of their algebraic relationships over the rational numbers. Leaving them alone works too. Those two operations form its Galois group: the symmetries of its roots.

The **inverse Galois problem** starts with the symmetry group and asks for the polynomial. The general problem remains open. IGP24 gave us a specific part to work on: polynomials with integer coefficients whose highest power is x²⁴. Each target asked for a particular group and a particular number of real roots. The polynomials had to be monic, with leading coefficient 1, and irreducible: they could not factor into lower-degree polynomials over the rationals.

There are 25,000 transitive groups of degree 24 in the catalog. Once you include the allowed real-root counts, there are 165,836 targets. The competition’s frozen baseline had examples for only 622 of them.[^1]

Think of it as a table with a great many empty cells. Each cell asks for a concrete mathematical object. Filling one does not automatically prove a new theorem: existence results already cover many of these groups. But an explicit polynomial gives researchers something they can calculate with, inspect, and use in another construction. That was the work the competition was asking people to do.

For close to a month, Durgesh Kumar and I tried to fill those cells. Our team, Dirac, ended up 14th on the published leaderboard. Getting there involved a lot of code, some useful mathematical ideas, and a thousand accepted answers that taught us we were asking the wrong question.

## The work between us

I’m Aishwarya, the founder of Dirac Labs. My day job is building quantum sensors. Durgesh was my batchmate in undergrad; in about two months, he starts a PhD in category theory. Neither of us is mathematics faculty. This was a problem we wanted to spend time on together.

Durgesh would suggest the mathematical strategies: structures we might exploit, extensions worth constructing, the “islands” where promising polynomials could live. I would take those ideas to Codex, turn them into programs, and put the larger searches on Google Cloud. The results came back into our next conversation.

That changed what I could bring back to Durgesh. If he suspected a construction was confined to the wrong family, I could help build the experiment that tested his suspicion. We could talk about the labels it returned, the cases it missed, and whether it deserved another run.

For the planning, I used GPT‑5.6 Pro as an orchestrator. I brought it Durgesh’s ideas, our formulas, returned labels, and bottlenecks. We worked through what might be going wrong and what to test next. When a plan was ready, I asked for a Markdown brief and passed it to Codex to implement.[^3] I was the person carrying the information between those conversations, the code, and the machines.

The useful handoff contained more than a suggestion. It named a construction, a small experiment, and the evidence that would justify a larger run. That last part became increasingly important.

### An idea had to make it through the whole loop.

- **Choose where to look**. Durgesh proposes a mathematical family. Pro helps turn the idea into a testable plan.

- **Make it run**. I pass the brief to Codex, work through the implementation, and organize the compute.

- **Find out what we made**. Exact algebra checks the candidates. Official verification identifies submitted examples.

- **Decide what comes next**. New pairs, repeats, failures, and costs go back into the ledger and the next conversation.

The results determine whether we expand a run, change the construction, or stop.

We built a **harness** around this loop: software to generate candidates, reject cheap failures and duplicates, run checks, submit selected polynomials, and record what came back. Its ledger remembered which group/real-root pairs we already had. Without that memory, a busy search could keep congratulating itself for finding the same things.

I ran Codex with persistent /goal instructions. A goal worked best when it could be resolved by an experiment: build a small pilot from this family, inspect its returned labels, compare them with the ledger, and expand only if the new pairs justified the cost. “Keep improving” was much less useful if we had not decided what improvement meant.

Some of the work was unglamorous. Heavy algebra jobs competed for memory. Verification and scoring did not always arrive together. Checkpoints made runs resumable; separate counts for generated, submitted, accepted, and scoreable candidates stopped a promising local report from becoming a claim of official success.

Even with those checks, a run could succeed at everything we had asked it to do and still teach us that the plan was poor.

## A thousand yeses. Almost the same answer.

One portfolio gave us 1,000 accepted polynomials. That sounds like a good day. Then we counted the groups: 989 of the thousand belonged to just three labels.[^3]

The verifier was saying yes. We had become very good at finding versions of what we already knew how to find.

The batch used degree-four constructions over degree-six starting fields, giving total degree 24. We had varied parameters and real-root behavior, so the list of coefficients looked broad. But the four main templates were all even quartics, of the form y⁴ + by² + c. Their built-in structure kept steering us into the same families.

I brought the actual templates and returned labels to Pro and asked it to explain the collapse. Its diagnosis focused on the restrictions we had preserved. It proposed a small general-quartic pilot, spread across different structural choices. It also warned that simply adding odd powers might send most candidates into another common family. We needed to test the change.

![Identical blue casts sit in a white tray, with distinct geometric forms beside it: a conceptual illustration of changing the construction.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/images/same-mould.png)

Different coefficients can preserve the same restrictive structure. The illustration captures the problem; the graph below shows the recorded results.

I asked for the diagnosis and plan as a Markdown file so Codex could implement it. That became GQ‑96, a planned portfolio of 96 candidates in two stages. The first stage returned **48 accepted polynomials across 14 group labels**, covering 48 distinct group/real-root pairs. Its three most common labels contained 12 rows: 25% of the batch, compared with 98.9% before.

The first checkpoint recorded 16 pairs that were new to our team. That was the useful number alongside the broader spread of labels. Forty-eight accepted rows did not mean 48 new discoveries, and the immediate scoring check was still pending at that checkpoint.[^3]

### A smaller experiment explored more evenly.

![The earlier 1,000-row portfolio put 98.9 percent in its top three group labels. The later 48-row pilot put 25 percent in its top three labels.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/figures/diversity.svg)

Share of each batch in its three most common group labels: 989 of 1,000 versus 12 of 48. These were different-sized, deliberately selected batches, not a controlled comparison of models. [See the data ↗](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/diversity.json)

This is the episode I keep coming back to when people ask what the AI contributed. We can follow it: a repetitive batch, an explanation tied to the formulas, a research brief, an implementation, and a pilot that explored more evenly. The explanation mattered because we could do something with it—and find out whether it helped.

## Here is one of the polynomials.

Other routes began with something we had already found. Durgesh’s approach gave us reason to look at familiar objects differently: the roots of one polynomial could supply the ingredients for another. Two routes recorded as F5 and F6 produced 49 distinct group/real-root pairs that we matched to accepted competition receipts.[^4]

Here is one of them. Start with an accepted degree-24 polynomial written as q(x²), where q has degree 12. Take the twelve roots of q and form their 66 unordered pairwise products. Make a polynomial with those products as its roots, then factor it over the rationals.

For this example, the factors have degrees 6, 12, and 48. Take the unique degree-12 factor, h, and substitute x² into it. The result is another degree-24 polynomial, now identified in the saved official receipt as **24T15308 with 20 real roots**.[^5]

### From a construction to an exact answer.

24T15308 / r = 20

$$
\begin{aligned}f(x)={}&x^{24}-65x^{22}-919x^{20}\\&+99\,720x^{18}-2\,091\,889x^{16}\\&+20\,925\,521x^{14}-115\,387\,013x^{12}\\&+361\,770\,394x^{10}-629\,434\,912x^{8}\\&+562\,276\,089x^{6}-201\,494\,301x^{4}\\&-10\,640\,675x^{2}+15\,405\,625.\end{aligned}
$$

PARI/GP replay reproduced the coefficients and checked degree, irreducibility, and real-root count. The Galois-group label comes from the [archived official receipt and action certificate](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/worked_example.json); Magma identification was not rerun for this article.

A verified result could become the starting point of another search. That made the ledger more than a list of successes. It was a collection of material we could return to, transform, and test again.

There were dead ends too. Alongside the productive F5 records, we found five additional zero-hit summaries covering 54 resolutions.[^4] A persistent agent could keep trying, but persistence alone could not tell us whether a family deserved another run. We had to decide what the evidence was saying.

![White contour-map islands connected by a red route on a dark blue ground. A conceptual illustration of exploring construction families.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/images/interlude-search-archipelago.png)

Durgesh’s “islands”: families of constructions that gave us a reason to search nearby. This is a conceptual landscape, not measured data.

## What were we actually climbing?

Hill climbing starts with a candidate, tries nearby alternatives, and keeps the improvements.[^7] The difficult part is often deciding what “nearby” and “better” should mean. In our search, a small change to a coefficient could destroy a useful property or leave us in exactly the same group.

Durgesh’s islands gave us more meaningful moves: a different extension, a sign choice, an action on roots, or a new use for a field already in the ledger. We could explore within a family, give productive families more compute, and move elsewhere when the results became repetitive. Much of the hill climbing happened in the search procedure itself.

IGP24 suited this way of working because it offered many separately checkable targets. We did not have to wait for one enormous proof to know anything. A pilot could tell us that a construction was too restrictive; an accepted polynomial could become a useful seed. The harness made that feedback available for the next decision.

Does that divide mathematics into problems you can hill-climb and problems you cannot? I don’t think the boundary is so clean. It depends on how you represent the question, which moves you allow, and what you can measure. Sometimes a search for examples, counterexamples, or better bounds gives a hard problem an iterative form. Finding that formulation may itself be the mathematical breakthrough. It also does not guarantee that a better score means you are closer to a proof.

## Then we started losing ground.

We reached an earlier checkpoint at rank 11. The published September 1 table places us **14th, with 30,426 scoreable group/real-root pairs and 405.86189 points**.[^2] More examples had not guaranteed a better position.

The competition was measuring something more demanding than how many polynomials we could submit. For each target, it discounted credit as more teams found it. It also rewarded smaller discriminants—a numerical invariant used to compare the examples. A correct polynomial could stay correct while the points it earned fell.

### What an accepted pair is worth

The score combines shared credit and discriminant quality.

$$
w=2^{1-k}\,\frac{\log D_0}{\log D}
$$

- **k**: Number of credited teams for this pair.

- **D**: Your team’s best official scoring discriminant.

- **D₀**: The smallest scoring discriminant among credited teams.

For a non-baseline pair held by one team, k = 1 and D = D₀, so it earns 1 point. Each additional credited team halves the sharing factor. Baseline pairs earn credit only after beating the baseline’s exact field discriminant; the baseline then counts as an additional team. The rules also specify which discriminant calculation applies.[^1]

[Read the full scoring rules ↗](https://competition.sair.foundation/competitions/igp24/evaluation-setup)

Between August 6 and the September 1 table, our credited coverage grew by 7,108 pairs while our score fell by about 273.812 points.[^6] The collection was growing. Our competitive position was not keeping up. This was another reason the harness needed to track more than acceptance: it had to help us notice when the things we were good at finding had become less valuable.

### Our coverage grew. Our score did not keep up.

![Across seven dated checkpoints, scoreable pairs increased to 30,426. Score peaked at the July 18 checkpoint at 1,160.148, then fell to 405.862 in the September 1 table.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/figures/trajectory.svg)

Seven selected checkpoints, July 4–September 1, extending beyond our period of active work. Lines connect observations, not daily measurements. The record does not isolate the effects of sharing, discriminants, and score recomputation. [See the data ↗](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/checkpoints.csv)

Fourteenth place means something to us. This is a competition whose leading team includes Gunter Malle and Jürgen Klüners, with deep expertise in computational number theory.[^2] Our number is a competition result, not a ranking of mathematicians. The 30,426 pairs are not 30,426 distinct groups or exclusive discoveries. They are a substantial collection of explicit examples, produced through a process we can describe and, in places, replay.

What changed for me was how much of Durgesh’s mathematics I could work on. I could take a construction, help turn it into a running experiment, and come back with something worth discussing. Sometimes the result supported the idea. Sometimes it showed that we needed a different one. AI made that exchange much more productive, and the checks kept us honest about what it had produced.

That is why I think accounts of AI in mathematics should spend more time on the work between the idea and the result. In our case, the contribution is visible in a failed batch, a diagnosis, a Markdown handoff, a better pilot, and polynomials that survived verification. Those are specific things another team can inspect and learn from.

## See you at Caltech.

We’re excited to attend the [Science x AI Summit at Caltech this Friday, September 11](https://www.caltech.edu/campus-life-events/calendar/science-x-ai-summit-2026-1). This is our account of close to a month spent on IGP24: two undergraduate batchmates, neither mathematics faculty, working with models and a lot of computational algebra, and finishing 14th on the published table.

We’re bringing the experiments as well as the result. We want to hear where other people’s loops worked, where they broke, and which mathematical questions might become approachable with a setup like this. If you’re there, we’d love to compare notes.

[^1]: [IGP24 overview](https://competition.sair.foundation/competitions/igp24/overview) and [evaluation rules](https://competition.sair.foundation/competitions/igp24/evaluation-setup).
[^2]: [Published leaderboard](https://competition.sair.foundation/competitions/igp24/leaderboard), table dated September 1, retrieved September 9 UTC / September 8 Pacific.
[^3]: [Quartic portfolio, first-stage pilot, and workflow provenance](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/diversity.json). The Pro setting is reported by Aishwarya; per-turn model metadata is unavailable.
[^4]: [F5/F6 matched receipts and selected-cohort limitations](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/f5-f6.json).
[^5]: [Construction, action certificate, and archived receipt](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/worked_example.json). [Fresh arithmetic replay](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/worked_example_replay.json); Magma identification was not rerun.
[^6]: [Seven dated checkpoints and their sources](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/checkpoints.csv).
[^7]: Poole and Mackworth, [Local Search](https://artint.info/3e/html/ArtInt3e.Ch4.S6.html), for hill climbing and its limits. The discussion about mathematical formulations is our interpretation.

Written with AI assistance from our conversations and experiment records. Images were made with Image Gen; graphs use recorded data. [Image prompts](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/illustration-prompts.json).

[Browse the public research archive](https://github.com/ash9241/team-dirac-igp24) · [Pro conversation and handoffs](https://github.com/ash9241/team-dirac-igp24/blob/main/conversations/README.md)
