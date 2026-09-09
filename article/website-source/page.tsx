/* eslint-disable @next/next/no-img-element */
import type { ReactNode } from "react";
import katex from "katex";
import { SharingExplorer } from "./essay-tools";

const repository = "https://github.com/ash9241/team-dirac-igp24";
const evidence = repository + "/blob/main/article/evidence/";
const example = repository + "/tree/main/examples/f5";

const board = "https://competition.sair.foundation/competitions/igp24/leaderboard";
const rules = "https://competition.sair.foundation/competitions/igp24/overview";
const summit = "https://www.caltech.edu/campus-life-events/calendar/science-x-ai-summit-2026-1";
const coefficients = "15405625,0,-10640675,0,-201494301,0,562276089,0,-629434912,0,361770394,0,-115387013,0,20925521,0,-2091889,0,99720,0,-919,0,-65,0,1";
const polynomial = "\\begin{aligned}f(x)={}&x^{24}-65x^{22}-919x^{20}\\\\&+99\\,720x^{18}-2\\,091\\,889x^{16}\\\\&+20\\,925\\,521x^{14}-115\\,387\\,013x^{12}\\\\&+361\\,770\\,394x^{10}-629\\,434\\,912x^{8}\\\\&+562\\,276\\,089x^{6}-201\\,494\\,301x^{4}\\\\&-10\\,640\\,675x^{2}+15\\,405\\,625.\\end{aligned}";

function Ref({n}:{n:number}) {
  return <sup className="reference"><a href={"#source-"+n} aria-label={"Source "+n}>{n}</a></sup>;
}
function Equation({tex}:{tex:string}) {
  return <div className="math-line" data-tex={tex} dangerouslySetInnerHTML={{__html:katex.renderToString(tex,{displayMode:true,throwOnError:true,output:"htmlAndMathml"})}}/>;
}
function Section({id,title,note,children}:{id:string,title?:string,note?:ReactNode,children:ReactNode}) {
  return <section id={id} className="story-section"><aside className="margin-note">{note}</aside><div className="prose">{title&&<h2>{title}</h2>}{children}</div></section>;
}
function Graph({name,title,alt,caption}:{name:string,title:string,alt:string,caption:string}) {
  return <figure className="graph wide"><h3>{title}</h3><picture><source media="(max-width:600px)" srcSet={"/figures/"+name+"-mobile.svg"}/><img src={"/figures/"+name+".svg"} alt={alt} loading="lazy" width="1100" height="460"/></picture><figcaption>{caption} <a href={evidence+(name==="diversity"?"diversity.json":"checkpoints.csv")}>See the data ↗</a></figcaption></figure>;
}

export default function Article() {
  return <main id="top">
    <a className="skip-link" href="#problem">Skip to the article</a>
    <header className="article-header">
      <img className="hero-art" src="/images/hero-wire-knot.png" alt="Fine silver and blue strands meeting in a suspended mathematical sculpture. A conceptual illustration." width="1672" height="941" fetchPriority="high"/>
      <div className="hero-date"><span>Aishwarya Das &amp; Durgesh Kumar</span><time dateTime="2026-09-08">September 8, 2026</time></div>
      <div className="hero-copy">
        <h1>Two batchmates<br/>walk into a<br/>maths competition.</h1>
        <p className="subtitle">A founder building quantum sensors, a future category theorist,<br className="desktop-break"/> and a month spent learning what AI could help us find.</p>
      </div>
      <a className="begin-link" href="#problem">The story <span aria-hidden="true">↓</span></a>
    </header>
    <div className="byline"><p>By <strong>Aishwarya Das</strong><br/>Research with <strong>Durgesh Kumar</strong></p><p>IGP24 · Degree 24<br/>GPT‑5.6 Pro, Codex &amp; computational algebra</p></div>
    <nav className="reading-nav" aria-label="In this article"><a href="#problem">The problem</a><a href="#turning-point">The experiment</a><a href="#polynomial">The polynomial</a><a href="#results">The score</a><a href="#friday">Caltech</a><a href={repository}>GitHub ↗</a></nav>

    <article id="essay">
      <Section id="problem" note={<><span className="margin-stat">24</span><p>The highest power of x in the polynomials we were trying to find.</p></>}>
        <p className="opening">The competition asked us to work backwards: choose how an equation’s roots should behave, then find an equation whose roots behave that way.</p>
        <p>Most of us meet a polynomial as something to solve. Take x² − 2 = 0. Its two roots are √2 and −√2. You can exchange them without changing their sum, their product, or any of their algebraic relationships over the rational numbers. Leaving them alone works too. Those two operations form its Galois group: the symmetries of its roots.</p>
        <p>The <strong>inverse Galois problem</strong> starts with the symmetry group and asks for the polynomial. The general problem remains open. IGP24 gave us a specific part to work on: polynomials with integer coefficients whose highest power is x²⁴. Each target asked for a particular group and a particular number of real roots. The polynomials had to be monic, with leading coefficient 1, and irreducible: they could not factor into lower-degree polynomials over the rationals.</p>
      </Section>
      <figure className="roots-study wide">
        <div className="roots-image"><img src="/images/symmetry-study.png" alt="Conceptual illustration of rearrangement using glass spheres on rotated acrylic plates." width="1448" height="1086" loading="lazy"/></div>
        <div className="roots-explanation"><h3>Two roots. A symmetry you can see.</h3><Equation tex={"x^2-2=0"}/><div className="root-swap"><span>−√2</span><span aria-hidden="true">⇄</span><span>√2</span></div><p>Exchange the roots.<br/>Their sum remains 0; their product remains −2.</p><small>This quadratic is the small example. IGP24 asks for degree 24.</small></div>
        <figcaption>The glass study illustrates rearrangement. The equation gives an exact example of the idea.</figcaption>
      </figure>
      <Section id="why-it-matters" note={<><p><strong>25,000</strong> groups</p><p><strong>165,836</strong> allowed group / real-root combinations</p><p><strong>622</strong> examples in the frozen baseline</p></>}>
        <p>There are 25,000 transitive groups of degree 24 in the catalog. Once you include the allowed real-root counts, there are 165,836 targets. The competition’s frozen baseline had examples for only 622 of them.<Ref n={1}/></p>
        <p>Think of it as a table with a great many empty cells. Each cell asks for a concrete mathematical object. Filling one does not automatically prove a new theorem: existence results already cover many of these groups. But an explicit polynomial gives researchers something they can calculate with, inspect, and use in another construction. That was the work the competition was asking people to do.</p>
        <p>For close to a month, Durgesh Kumar and I tried to fill those cells. Our team, Dirac, ended up 14th on the published leaderboard. Getting there involved a lot of code, some useful mathematical ideas, and a thousand accepted answers that taught us we were asking the wrong question.</p>
      </Section>

      <Section id="people" title="The work between us" note={<><p><strong>Aishwarya Das</strong><br/>Founder, Dirac Labs</p><p><strong>Durgesh Kumar</strong><br/>Starting a PhD in category theory</p></>}>
        <p>I’m Aishwarya, the founder of Dirac Labs. My day job is building quantum sensors. Durgesh was my batchmate in undergrad; in about two months, he starts a PhD in category theory. Neither of us is mathematics faculty. This was a problem we wanted to spend time on together.</p>
        <p>Durgesh would suggest the mathematical strategies: structures we might exploit, extensions worth constructing, the “islands” where promising polynomials could live. I would take those ideas to Codex, turn them into programs, and put the larger searches on Google Cloud. The results came back into our next conversation.</p>
        <p>That changed what I could bring back to Durgesh. If he suspected a construction was confined to the wrong family, I could help build the experiment that tested his suspicion. We could talk about the labels it returned, the cases it missed, and whether it deserved another run.</p>
        <p>For the planning, I used GPT‑5.6 Pro as an orchestrator. I brought it Durgesh’s ideas, our formulas, returned labels, and bottlenecks. We worked through what might be going wrong and what to test next. When a plan was ready, I asked for a Markdown brief and passed it to Codex to implement.<Ref n={3}/> I was the person carrying the information between those conversations, the code, and the machines.</p>
        <p>The useful handoff contained more than a suggestion. It named a construction, a small experiment, and the evidence that would justify a larger run. That last part became increasingly important.</p>
      </Section>

      <figure className="harness-diagram wide">
        <h3>An idea had to make it through the whole loop.</h3>
        <ol>
          <li><span aria-hidden="true">1</span><strong>Choose where to look</strong><p>Durgesh proposes a mathematical family. Pro helps turn the idea into a testable plan.</p></li>
          <li><span aria-hidden="true">2</span><strong>Make it run</strong><p>I pass the brief to Codex, work through the implementation, and organize the compute.</p></li>
          <li><span aria-hidden="true">3</span><strong>Find out what we made</strong><p>Exact algebra checks the candidates. Official verification identifies submitted examples.</p></li>
          <li><span aria-hidden="true">4</span><strong>Decide what comes next</strong><p>New pairs, repeats, failures, and costs go back into the ledger and the next conversation.</p></li>
        </ol>
        <div className="feedback"><span aria-hidden="true">↶</span><p>The results determine whether we expand a run, change the construction, or stop.</p></div>
      </figure>

      <Section id="harness" note={<><p>The harness was the software that made the experiment repeatable—and its results hard to lose.</p></>}>
        <p>We built a <strong>harness</strong> around this loop: software to generate candidates, reject cheap failures and duplicates, run checks, submit selected polynomials, and record what came back. Its ledger remembered which group/real-root pairs we already had. Without that memory, a busy search could keep congratulating itself for finding the same things.</p>
        <p>I ran Codex with persistent <code>/goal</code> instructions. A goal worked best when it could be resolved by an experiment: build a small pilot from this family, inspect its returned labels, compare them with the ledger, and expand only if the new pairs justified the cost. “Keep improving” was much less useful if we had not decided what improvement meant.</p>
        <p>Some of the work was unglamorous. Heavy algebra jobs competed for memory. Verification and scoring did not always arrive together. Checkpoints made runs resumable; separate counts for generated, submitted, accepted, and scoreable candidates stopped a promising local report from becoming a claim of official success.</p>
        <p>Even with those checks, a run could succeed at everything we had asked it to do and still teach us that the plan was poor.</p>
      </Section>

      <Section id="turning-point" title="A thousand yeses. Almost the same answer." note={<><span className="margin-stat">989</span><p>of 1,000 accepted polynomials landed in just three group labels.</p></>}>
        <p>One portfolio gave us 1,000 accepted polynomials. That sounds like a good day. Then we counted the groups: 989 of the thousand belonged to just three labels.<Ref n={3}/></p>
        <p>The verifier was saying yes. We had become very good at finding versions of what we already knew how to find.</p>
        <p>The batch used degree-four constructions over degree-six starting fields, giving total degree 24. We had varied parameters and real-root behavior, so the list of coefficients looked broad. But the four main templates were all even quartics, of the form y⁴ + by² + c. Their built-in structure kept steering us into the same families.</p>
        <p>I brought the actual templates and returned labels to Pro and asked it to explain the collapse. Its diagnosis focused on the restrictions we had preserved. It proposed a small general-quartic pilot, spread across different structural choices. It also warned that simply adding odd powers might send most candidates into another common family. We needed to test the change.</p>
      </Section>
      <figure className="search-study wide"><img src="/images/same-mould.png" alt="Identical blue casts sit in a white tray, with distinct geometric forms beside it: a conceptual illustration of changing the construction." width="1672" height="941" loading="lazy"/><figcaption>Different coefficients can preserve the same restrictive structure. The illustration captures the problem; the graph below shows the recorded results.</figcaption></figure>
      <Section id="the-next-batch">
        <p>I asked for the diagnosis and plan as a Markdown file so Codex could implement it. That became GQ‑96, a planned portfolio of 96 candidates in two stages. The first stage returned <strong>48 accepted polynomials across 14 group labels</strong>, covering 48 distinct group/real-root pairs. Its three most common labels contained 12 rows: 25% of the batch, compared with 98.9% before.</p>
        <p>The first checkpoint recorded 16 pairs that were new to our team. That was the useful number alongside the broader spread of labels. Forty-eight accepted rows did not mean 48 new discoveries, and the immediate scoring check was still pending at that checkpoint.<Ref n={3}/></p>
      </Section>
      <Graph name="diversity" title="A smaller experiment explored more evenly." alt="The earlier 1,000-row portfolio put 98.9 percent in its top three group labels. The later 48-row pilot put 25 percent in its top three labels." caption="Share of each batch in its three most common group labels: 989 of 1,000 versus 12 of 48. These were different-sized, deliberately selected batches, not a controlled comparison of models."/>
      <Section id="what-changed">
        <p>This is the episode I keep coming back to when people ask what the AI contributed. We can follow it: a repetitive batch, an explanation tied to the formulas, a research brief, an implementation, and a pilot that explored more evenly. The explanation mattered because we could do something with it—and find out whether it helped.</p>
      </Section>

      <Section id="polynomial" title="Here is one of the polynomials." note={<><p><strong>24T15308</strong><br/>20 real roots</p><p>An accepted example, with a construction you can replay.</p></>}>
        <p>Other routes began with something we had already found. Durgesh’s approach gave us reason to look at familiar objects differently: the roots of one polynomial could supply the ingredients for another. Two routes recorded as F5 and F6 produced 49 distinct group/real-root pairs that we matched to accepted competition receipts.<Ref n={4}/></p>
        <p>Here is one of them. Start with an accepted degree-24 polynomial written as q(x²), where q has degree 12. Take the twelve roots of q and form their 66 unordered pairwise products. Make a polynomial with those products as its roots, then factor it over the rationals.</p>
        <p>For this example, the factors have degrees 6, 12, and 48. Take the unique degree-12 factor, h, and substitute x² into it. The result is another degree-24 polynomial, now identified in the saved official receipt as <strong>24T15308 with 20 real roots</strong>.<Ref n={5}/></p>
      </Section>
      <figure className="polynomial-panel wide" aria-labelledby="polynomial-title">
        <div className="polynomial-top"><h3 id="polynomial-title">From a construction to an exact answer.</h3><span>24T15308 / r = 20</span></div>
        <div className="construction-path">
          <div><strong>q(x²)</strong><span>Start with 24T19741</span></div><b aria-hidden="true">→</b>
          <div><strong>66 products</strong><span>Pair the twelve roots of q</span></div><b aria-hidden="true">→</b>
          <div><strong>6 · <mark>12</mark> · 48</strong><span>Choose the degree-12 factor h</span></div><b aria-hidden="true">→</b>
          <div><strong>f(x) = h(x²)</strong><span>Return to degree 24</span></div>
        </div>
        <div className="exact-polynomial" data-coefficients={coefficients}><Equation tex={polynomial}/></div>
        <div className="polynomial-checks"><span>Degree <strong>24</strong></span><span>Irreducible <strong>Yes</strong></span><span>Real roots <strong>20</strong></span><a href={example}>Reproduce on GitHub <span aria-hidden="true">↗</span></a></div>
        <figcaption>PARI/GP replay reproduced the coefficients and checked degree, irreducibility, and real-root count. The Galois-group label comes from the <a href={evidence+"worked_example.json"}>archived official receipt and action certificate</a>; Magma identification was not rerun for this article.</figcaption>
      </figure>
      <Section id="reusing-results">
        <p>A verified result could become the starting point of another search. That made the ledger more than a list of successes. It was a collection of material we could return to, transform, and test again.</p>
        <p>There were dead ends too. Alongside the productive F5 records, we found five additional zero-hit summaries covering 54 resolutions.<Ref n={4}/> A persistent agent could keep trying, but persistence alone could not tell us whether a family deserved another run. We had to decide what the evidence was saying.</p>
      </Section>

      <figure className="landscape"><img src="/images/interlude-search-archipelago.png" alt="White contour-map islands connected by a red route on a dark blue ground. A conceptual illustration of exploring construction families." width="1672" height="941" loading="lazy"/><figcaption>Durgesh’s “islands”: families of constructions that gave us a reason to search nearby. This is a conceptual landscape, not measured data.</figcaption></figure>
      <Section id="hill-climbing" title="What were we actually climbing?" note={<><p>Propose a change.<br/>Evaluate it.<br/>Keep improvements.<br/>Try again.</p></>}>
        <p>Hill climbing starts with a candidate, tries nearby alternatives, and keeps the improvements.<Ref n={7}/> The difficult part is often deciding what “nearby” and “better” should mean. In our search, a small change to a coefficient could destroy a useful property or leave us in exactly the same group.</p>
        <p>Durgesh’s islands gave us more meaningful moves: a different extension, a sign choice, an action on roots, or a new use for a field already in the ledger. We could explore within a family, give productive families more compute, and move elsewhere when the results became repetitive. Much of the hill climbing happened in the search procedure itself.</p>
        <p>IGP24 suited this way of working because it offered many separately checkable targets. We did not have to wait for one enormous proof to know anything. A pilot could tell us that a construction was too restrictive; an accepted polynomial could become a useful seed. The harness made that feedback available for the next decision.</p>
        <p>Does that divide mathematics into problems you can hill-climb and problems you cannot? I don’t think the boundary is so clean. It depends on how you represent the question, which moves you allow, and what you can measure. Sometimes a search for examples, counterexamples, or better bounds gives a hard problem an iterative form. Finding that formulation may itself be the mathematical breakthrough. It also does not guarantee that a better score means you are closer to a proof.</p>
      </Section>

      <Section id="results" title="Then we started losing ground." note={<><span className="margin-stat">11 → 14</span><p>An earlier rank checkpoint, followed by our position on the published table.</p></>}>
        <p>We reached an earlier checkpoint at rank 11. The published September 1 table places us <strong>14th, with 30,426 scoreable group/real-root pairs and 405.86189 points</strong>.<Ref n={2}/> More examples had not guaranteed a better position.</p>
        <p>The competition was measuring something more demanding than how many polynomials we could submit. For each target, it discounted credit as more teams found it. It also rewarded smaller discriminants—a numerical invariant used to compare the examples. A correct polynomial could stay correct while the points it earned fell.</p>
      </Section>
      <section className="scoring-panel wide" aria-labelledby="scoring-title">
        <div className="scoring-heading"><h3 id="scoring-title">What an accepted pair is worth</h3><p>The score combines shared credit and discriminant quality.</p></div>
        <Equation tex={"w=2^{1-k}\\,\\frac{\\log D_0}{\\log D}"}/>
        <dl className="score-definitions"><div><dt>k</dt><dd>Number of credited teams for this pair.</dd></div><div><dt>D</dt><dd>Your team’s best official scoring discriminant.</dd></div><div><dt>D₀</dt><dd>The smallest scoring discriminant among credited teams.</dd></div></dl>
        <SharingExplorer/>
        <p className="scoring-rule-note">For a non-baseline pair held by one team, k = 1 and D = D₀, so it earns 1 point. Each additional credited team halves the sharing factor. Baseline pairs earn credit only after beating the baseline’s exact field discriminant; the baseline then counts as an additional team. The rules also specify which discriminant calculation applies.<Ref n={1}/></p>
        <a className="rules-link" href="https://competition.sair.foundation/competitions/igp24/evaluation-setup">Read the full scoring rules ↗</a>
      </section>
      <Section id="the-moving-score">
        <p>Between August 6 and the September 1 table, our credited coverage grew by 7,108 pairs while our score fell by about 273.812 points.<Ref n={6}/> The collection was growing. Our competitive position was not keeping up. This was another reason the harness needed to track more than acceptance: it had to help us notice when the things we were good at finding had become less valuable.</p>
      </Section>
      <Graph name="trajectory" title="Our coverage grew. Our score did not keep up." alt="Across seven dated checkpoints, scoreable pairs increased to 30,426. Score peaked at the July 18 checkpoint at 1,160.148, then fell to 405.862 in the September 1 table." caption="Seven selected checkpoints, July 4–September 1, extending beyond our period of active work. Lines connect observations, not daily measurements. The record does not isolate the effects of sharing, discriminants, and score recomputation."/>
      <div className="result-strip wide"><div><strong>14th</strong><span>published rank</span></div><div><strong>30,426</strong><span>scoreable pairs</span></div><div><strong>405.862</strong><span>points, rounded</span></div><a href={board}>Official leaderboard ↗</a></div>
      <Section id="what-it-meant">
        <p>Fourteenth place means something to us. This is a competition whose leading team includes Gunter Malle and Jürgen Klüners, with deep expertise in computational number theory.<Ref n={2}/> Our number is a competition result, not a ranking of mathematicians. The 30,426 pairs are not 30,426 distinct groups or exclusive discoveries. They are a substantial collection of explicit examples, produced through a process we can describe and, in places, replay.</p>
        <p>What changed for me was how much of Durgesh’s mathematics I could work on. I could take a construction, help turn it into a running experiment, and come back with something worth discussing. Sometimes the result supported the idea. Sometimes it showed that we needed a different one. AI made that exchange much more productive, and the checks kept us honest about what it had produced.</p>
        <p>That is why I think accounts of AI in mathematics should spend more time on the work between the idea and the result. In our case, the contribution is visible in a failed batch, a diagnosis, a Markdown handoff, a better pilot, and polynomials that survived verification. Those are specific things another team can inspect and learn from.</p>
      </Section>

      <section id="friday" className="closing">
        <div className="closing-inner"><h2>See you<br/>at Caltech.</h2><div><p>We’re excited to attend the <a href={summit}>Science x AI Summit at Caltech this Friday, September 11</a>. This is our account of close to a month spent on IGP24: two undergraduate batchmates, neither mathematics faculty, working with models and a lot of computational algebra, and finishing 14th on the published table.</p><p>We’re bringing the experiments as well as the result. We want to hear where other people’s loops worked, where they broke, and which mathematical questions might become approachable with a setup like this. If you’re there, we’d love to compare notes.</p></div></div>
      </section>
    </article>

    <footer className="appendix" aria-labelledby="appendix-title">
      <div className="appendix-intro"><h2 id="appendix-title">The work behind<br/>the story.</h2><p>Data, construction, and checks, so you can follow an example all the way through.</p><div className="downloads"><a href={repository}>Explore the public research archive <span>↗</span></a><a href="/downloads/dirac-replay.zip" download>Download the polynomial replay <span>↓</span></a><a href="/downloads/two-batchmates.md" download>Download the article text <span>↓</span></a></div><p className="credits">Written with AI assistance from our conversations and experiment records. Images were made with Image Gen; graphs use recorded data. <a href={evidence+"illustration-prompts.json"}>Image prompts</a>.</p></div>
      <div><ol className="sources">
        <li id="source-1"><a href={rules}>IGP24 overview</a> and <a href="https://competition.sair.foundation/competitions/igp24/evaluation-setup">evaluation rules</a>: targets, baseline, verification, and scoring.</li>
        <li id="source-2"><a href={board}>Published leaderboard</a>. Table dated September 1, 2026, retrieved September 9 UTC / September 8 Pacific. <a href={evidence+"leaderboard.json"}>Saved team extract</a>.</li>
        <li id="source-3"><a href={evidence+"diversity.json"}>Quartic portfolio and first-stage pilot</a>, with workflow provenance. <a href={repository+"/blob/main/conversations/README.md"}>Pro conversation and implementation handoffs</a>. GPT‑5.6 Pro is the setting reported by Aishwarya; per-turn model metadata is unavailable. This is an account of the workflow, not a controlled ablation of its components.</li>
        <li id="source-4"><a href={evidence+"f5-f6.json"}>F5/F6 results</a>: 49 distinct pairs matched to accepted receipts, plus selected-cohort and failed-run records.</li>
        <li id="source-5"><a href={evidence+"worked_example.json"}>Worked example and archived receipt</a>; <a href={evidence+"worked_example_replay.json"}>PARI/GP replay result</a>. The group identification was not rerun in this audit.</li>
        <li id="source-6"><a href={evidence+"checkpoints.csv"}>Historical checkpoints</a>, with a source for each observation.</li>
        <li id="source-7">Poole and Mackworth, <a href="https://artint.info/3e/html/ArtInt3e.Ch4.S6.html">Local Search</a>, for hill climbing and its limits. The discussion of mathematical formulations is our interpretation.</li>
      </ol><a className="back-top" href="#top">Back to the beginning ↑</a></div>
    </footer>
  </main>;
}
