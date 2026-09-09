# Working with AI on the Inverse Galois Problem

[Read the illustrated website](https://team-dirac-igp24.vercel.app/) · [Explore the research archive](https://github.com/ash9241/team-dirac-igp24)

By [Aishwarya Das](https://x.com/anshu4321). Research with [Durgesh Kumar](https://x.com/contextuality).
9th September, 2026.

The competition asked us to work backwards: choose how an equation’s roots should behave, then find an equation whose roots behave that way.

Take the equation x² = 2. It has two answers: √2 and −√2, because squaring either gives 2. These answers are called its **roots**. Galois theory asks a different question: **which roots can exchange places without breaking the arithmetic?**

### Two roots. Both pass the same test.

$$
x^2=2
$$

→

Use √2

$$
(\sqrt{2})^2=2
$$

Use −√2

$$
(-\sqrt{2})^2=2
$$

The roots are the values that make the equation true.

If two roots add to zero, their replacements must still add to zero. The same goes for every relationship you can express using addition, multiplication, and rational numbers—whole numbers and fractions. A swap must preserve all of those relationships at once.

To check this properly, we include the roots, the fractions, and every number we can build from them by ordinary arithmetic, with no division by zero. Mathematicians call this number system the **splitting field**.

### Build the number system around the roots.

Start with **All fractions and ±√2** →

Keep applying **+   −   ×   ÷**

Some numbers inside it

- ½

- √2

- −√2

- 1 + √2

- 3 − 2√2

- …

Include every result the four operations can produce, with no division by zero. This collection has infinitely many numbers; the roots are only the starting ingredients.

An allowed swap must work consistently across this whole system. It has to preserve addition and multiplication, keep the fractions fixed, and be reversible. Such a rearrangement is called an **automorphism**.[^8]

The **Galois group** is the collection of all these allowed rearrangements, including doing nothing. We can combine two by performing one after the other, and every one can be undone. Together, they describe the algebraic symmetry of the roots.

### For x² = 2, the group has just two moves.

**Leave everything alone**

Every number stays fixed.

$$
\sqrt{2}\mapsto\sqrt{2}
$$

**Swap the roots**

Every √2 term changes sign. Fractions stay fixed.

$$
\begin{aligned}\sqrt{2}&\mapsto-\sqrt{2}\\1+\sqrt{2}&\mapsto1-\sqrt{2}\\\tfrac12&\mapsto\tfrac12\end{aligned}
$$

A swap followed by another swap brings every number back. These two moves form the Galois group for this example.

The **inverse Galois problem** reverses the task. Start with any finite group: a finite collection of symmetries that can be combined and undone. Can we find an equation built from powers of x and whole numbers or fractions whose Galois group is exactly that group? Nobody knows how to do this for every finite group. The general problem remains open.

### The inverse problem changes where we start.

**Usual direction** An equation  →  Find its symmetries

**Inverse problem** Wanted symmetries  →  Find an equation

IGP24 gave us a specific part to work on: polynomials with integer coefficients whose highest power is x²⁴. Each target asked for a particular group acting on the 24 roots, and a particular number of real roots. The polynomials had to be monic, with leading coefficient 1. They also had to be irreducible: they could not factor into lower-degree polynomials over the rationals.

### Some swaps work. Others break the arithmetic.

Here is the same test for x⁴ = 2. We use α for the positive number whose fourth power is 2. The symbol i is the imaginary unit, defined by i² = −1. There are four roots: α, −α, iα, and −iα. The diagram places the real roots horizontally and the imaginary roots vertically.

![For x to the fourth minus two, complex conjugation swaps i alpha and minus i alpha and fixes the real roots. This is allowed. Swapping alpha and i alpha alone breaks alpha plus minus alpha equals zero. Eight of the 24 root permutations belong to the Galois group.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/figures/galois-symmetries.svg)

On the left, the two imaginary roots trade places while the real roots stay fixed. This is called complex conjugation, and it preserves every required relationship. On the right, α changes but −α does not: two numbers that used to add to zero no longer do. There are 24 ways to rearrange four roots, but only 8 pass every test.[^8]

There are 25,000 transitive groups of degree 24 in the catalog. Once you include the allowed real-root counts, there are 165,836 targets. The competition’s frozen baseline had examples for only 622 of them.[^1]

Think of it as a table with a great many empty cells. Each cell asks for a concrete mathematical object. Filling one does not automatically prove a new theorem: existence results already cover many of these groups. But an explicit polynomial gives researchers something they can calculate with, inspect, and use in another construction. That was the work the competition was asking people to do.

For close to a month, Durgesh Kumar and I tried to fill those cells. Our team, Dirac, ended up 14th on the published leaderboard. Getting there involved a lot of code, some useful mathematical ideas, and a thousand accepted answers that taught us we were asking the wrong question.

## The work between us

I’m [Aishwarya](https://x.com/anshu4321), co-founder of [Dirac Labs](https://www.diraclabs.com/), where we’re building quantum sensors to help machines navigate underwater, beyond the reach of GPS. [Durgesh Kumar](https://x.com/contextuality) and I were batchmates in undergrad. He’s just finished a master’s in category theory and starts his PhD in about two months. IGP24 gave us a chance to put his mathematical instincts and my interest in machine learning to work on the same problem. He brought constructions worth exploring; I helped turn them into experiments we could actually run.

Durgesh would suggest the mathematical strategies: structures we might exploit, extensions worth constructing, the “islands” where promising polynomials could live. I would take those ideas to Codex, turn them into programs, and put the larger searches on Google Cloud. The results came back into our next conversation.

That changed what I could bring back to Durgesh. If he suspected a construction was confined to the wrong family, I could help build the experiment that tested his suspicion. We could talk about the labels it returned, the cases it missed, and whether it deserved another run.

For the planning, I used GPT‑5.6 Pro as an orchestrator. I brought it Durgesh’s ideas, our formulas, returned labels, and bottlenecks. We worked through what might be going wrong and what to test next. When a plan was ready, I asked for a Markdown brief and passed it to Codex to implement.[^3] I was the person carrying the information between those conversations, the code, and the machines.

The useful handoff contained more than a suggestion. It named a construction, a small experiment, and the evidence that would justify a larger run. That last part became increasingly important.

### The next search began before the next submission.

- **Choose a family**. Durgesh proposes a construction. Pro helps turn it into a bounded experiment.

- **Generate candidates**. I work with Codex to implement the plan and run the local or cloud search.

- **Check locally**. Check validity and real-root count. Use the construction and local group predictor to identify promising targets.

- **Estimate the gain**. Compare candidates with our ledger and a fresh leaderboard snapshot. Rank the expected new value.

- **Submit and reconcile**. Submit the selected batch. Record the official group labels, real-root counts, and scoring status.

- **Update the next run**. Feed the returned labels into the predictor and update the ledger. Expand, change, or stop the family.

Local checks and score forecasts guide submission. Official results then correct the information used to choose the next batch.

We built a **harness** around this loop. Our local verifier checked things such as degree, monicity, irreducibility, and real-root count. For group identification, we also used the construction’s structure and a local predictor informed by earlier official results. A predicted label remained a prediction until it was justified or confirmed by the competition’s verifier.

The **dynamic ledger** remembered our candidates, submissions, and verified pairs. It also held dated snapshots of the public targets: how many teams held a pair and the best known scoring discriminant. Before submitting a wave, the controller refreshed those targets. The scheduler combined that information with our confidence in a candidate’s label and its estimated discriminant to forecast the score it might add. It filtered out pairs we already held and accounted for repeated attempts at the same target.[^9]

That gave us feedback before each batch went in. A locally valid polynomial could still offer little expected gain. When official results came back, they updated the ledger and supplied new examples for the group predictor. We could then reassess the family against what we now knew. The forecast guided a decision; the official result told us what had actually counted.

I ran Codex with persistent /goal instructions. A goal worked best when an experiment could resolve it: build a small pilot, inspect its returned labels, and compare them with the ledger. Expand only if the new pairs justified the cost. “Keep improving” was much less useful if we had not decided what improvement meant.

Some of the work was unglamorous. Heavy algebra jobs competed for memory. Verification and scoring did not always arrive together. Checkpoints made runs resumable. We kept separate counts for generated, submitted, accepted, and scoreable candidates, so a promising local report did not become a claim of official success.

Even with those checks, a run could succeed at everything we had asked it to do and still teach us that the plan was poor.

## A thousand yeses. Almost the same answer.

One portfolio gave us 1,000 accepted polynomials. That sounds like a good day. Then we counted the groups: 989 of the thousand belonged to just three labels.[^3]

The verifier was saying yes. We had become very good at finding versions of what we already knew how to find.

The batch used degree-four constructions over degree-six starting fields, giving total degree 24. We had varied parameters and real-root behavior, so the list of coefficients looked broad. But the four main templates were all even quartics, of the form y⁴ + by² + c. Their built-in structure kept steering us into the same families.

I brought the actual templates and returned labels to Pro and asked it to explain the collapse. Its diagnosis focused on the restrictions we had preserved. It proposed a small general-quartic pilot, spread across different structural choices. It also warned that simply adding odd powers might send most candidates into another common family. We needed to test the change.

### The restriction was in the formula.

Start with an even quartic

$$
y^4+b y^2+c
$$

Set z = y²

$$
z^2+bz+c
$$

Each z-root gives a ± pair

$$
\pm\sqrt{z_1},\quad\pm\sqrt{z_2}
$$

Changing b and c preserves this two-stage construction: solve a quadratic in z, then take square roots. The ± pairing is built in. The next pilot tested quartics that could break that restriction; the graph below shows what came back.

I asked for the diagnosis and plan as a Markdown file so Codex could implement it. That became GQ‑96, a planned portfolio of 96 candidates in two stages. The first stage returned **48 accepted polynomials across 14 group labels**. They covered 48 distinct group/real-root pairs. Its three most common labels contained 12 rows: 25% of the batch, compared with 98.9% before.

The first checkpoint recorded 16 pairs that were new to our team. That was the useful number alongside the broader spread of labels. Forty-eight accepted rows did not mean 48 new discoveries, and the immediate scoring check was still pending at that checkpoint.[^3]

### A smaller experiment explored more evenly.

![The earlier 1,000-row portfolio put 98.9 percent in its top three group labels. The later 48-row pilot put 25 percent in its top three labels.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/figures/diversity.svg)

Share of each batch in its three most common group labels: 989 of 1,000 versus 12 of 48. These were different-sized, deliberately selected batches, not a controlled comparison of models. [See the data ↗](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/diversity.json)

This is the episode I keep coming back to when people ask what the AI contributed. We can follow it: a repetitive batch, an explanation tied to the formulas, a research brief, an implementation, and a pilot that explored more evenly. The explanation mattered because we could do something with it—and find out whether it helped.

## Here is one of the polynomials.

Other routes began with something we had already found. Durgesh’s approach gave us reason to look at familiar objects differently: the roots of one polynomial could supply the ingredients for another. Two routes recorded as F5 and F6 produced 49 distinct group/real-root pairs that we matched to accepted competition receipts.[^4]

Here is one of them. Start with an accepted degree-24 polynomial written as q(x²), where q has degree 12. Take the twelve roots of q and form their 66 unordered pairwise products. Make a polynomial with those products as its roots. Then factor it over the rationals.

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

Andrej Karpathy helped popularise the term **autoresearch** with a small, concrete example. An AI agent changes the code used to train a language model, runs a five-minute training experiment, and checks how well the resulting model predicts text it was not trained on. It retains changes that improve that result, reverts unsuccessful ones, and repeats.[^10] The human sets the instructions, the time budget, and the test. The agent can then carry out many experiments without waiting for a person between each one.

Autoresearch lets an agent run that cycle of experiments and feedback. **Hill climbing is one way to decide which changes to keep.** The familiar picture is below: each position represents a possible candidate, and height represents how well it scores. Moving uphill gets you to something better nearby. It can also leave you on top of a small hill, with a higher one across a valley. A fresh starting point or a different kind of move can help the search explore elsewhere. Our persistent runs had this rhythm, with Durgesh and me still choosing mathematical directions and reviewing what the experiments meant.

### Getting uphill is easier than finding the highest hill.

Keep a change when it improves the result. Repeat until nearby changes stop helping.

![A schematic search landscape. Blue steps climb from a starting point to a local peak. Beyond a valley lies a higher, global peak; farther right is a flat plateau. Ordinary uphill moves can stop at the local peak even though the global peak is better.](https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article/figures/hill-climbing.svg)

A local maximum is better than its neighbors. The global maximum is the best point across the whole landscape. A plateau offers little guidance because nearby choices score the same. This is a schematic of the search idea, not a plot of our IGP24 results. Our constructions form a much more complicated space, and the competition’s scoring landscape also changes as other teams submit.[^7]

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

[^1]: [IGP24 overview](https://competition.sair.foundation/competitions/igp24/overview) and [evaluation rules](https://competition.sair.foundation/competitions/igp24/evaluation-setup): targets, baseline, verification, and scoring.
[^2]: [Published leaderboard](https://competition.sair.foundation/competitions/igp24/leaderboard). Table dated September 1, 2026, retrieved September 9 UTC / September 8 Pacific. [Saved team extract](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/leaderboard.json).
[^3]: [Quartic portfolio and first-stage pilot](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/diversity.json), with workflow provenance. [Pro conversation and implementation handoffs](https://github.com/ash9241/team-dirac-igp24/blob/main/conversations/README.md). GPT‑5.6 Pro is the setting reported by Aishwarya; per-turn model metadata is unavailable. This is an account of the workflow, not a controlled ablation of its components.
[^4]: [F5/F6 results](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/f5-f6.json): 49 distinct pairs matched to accepted receipts, plus selected-cohort and failed-run records.
[^5]: [Worked example and archived receipt](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/worked_example.json); [PARI/GP replay result](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/worked_example_replay.json). The group identification was not rerun in this audit.
[^6]: [Historical checkpoints](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/checkpoints.csv), with a source for each observation.
[^7]: Poole and Mackworth, [Local Search](https://artint.info/3e/html/ArtInt3e.Ch4.S6.html), for hill climbing and its limits. The discussion of mathematical formulations is our interpretation.
[^8]: J. S. Milne, [Fields and Galois Theory](https://www.jmilne.org/math/CourseNotes/FT.pdf), chapters 2–3: splitting fields and automorphisms. For x⁴ − 2, Eisenstein’s criterion gives [ℚ(α) : ℚ] = 4. This field is real, so adjoining i doubles the degree to 8.
[^9]: Archived harness code: [local validity gates](https://github.com/ash9241/team-dirac-igp24/blob/main/research/routeA/submit_exploration_batch.py), [ledger](https://github.com/ash9241/team-dirac-igp24/blob/main/research/routeA/ledger.py), [expected-value scheduler](https://github.com/ash9241/team-dirac-igp24/blob/main/research/routeA/scheduler.py), and [wave controller and marginal forecast](https://github.com/ash9241/team-dirac-igp24/blob/main/research/routeA/controller.py). The [research runbook](https://github.com/ash9241/team-dirac-igp24/blob/main/research/IGP24_Rank_Climbing_System.md) records the local group predictor and its updates from official labels.
[^10]: Andrej Karpathy’s [autoresearch](https://github.com/karpathy/autoresearch): the March 2026 project and its [experiment instructions](https://github.com/karpathy/autoresearch/blob/master/program.md). The comparison with our IGP24 workflow is ours.

Written with AI assistance from our conversations and experiment records. The hero and landscape were made with Image Gen. The diagrams explain the algebra; graphs use recorded data. [Image prompts](https://github.com/ash9241/team-dirac-igp24/blob/main/article/evidence/illustration-prompts.json).
