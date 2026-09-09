"use client";
import { useState } from "react";
import { pairScenario } from "./scoring.mjs";

const number = (value:number) => value.toLocaleString("en-US",{maximumFractionDigits:5});

export function SharingExplorer() {
  const [teams,setTeams] = useState(1);
  const [power,setPower] = useState(1);
  const scenario = pairScenario(teams,power);
  function changeTeams(value:number) {
    setTeams(value);
    if(value===1) setPower(1);
  }
  return <div className="sharing-explorer">
    <div className="explorer-head"><h4>Try the scoring mechanism.</h4><span>A hypothetical non-baseline pair</span></div>
    <div className="explorer-body">
      <div className="explorer-inputs">
        <div><label htmlFor="credited-teams">Credited teams <strong data-teams-value>{teams}</strong></label><input id="credited-teams" type="range" min="1" max="6" step="1" value={teams} onChange={event=>changeTeams(Number(event.target.value))}/><div className="range-extents"><span>1 team</span><span>6 teams</span></div></div>
        <div><label htmlFor="discriminant-power">Your discriminant <strong>D₀<sup data-power-value>{scenario.power===1?"":scenario.power}</sup></strong></label><input id="discriminant-power" type="range" min="1" max="4" step="1" value={scenario.power} disabled={teams===1} aria-describedby="score-help" aria-valuetext={scenario.power===1?"Matches the best discriminant":"Reference discriminant raised to power "+scenario.power} onChange={event=>setPower(Number(event.target.value))}/><div className="range-extents"><span>Matches D₀</span><span>D₀⁴</span></div></div>
      </div>
      <output className="score-output" htmlFor="credited-teams discriminant-power" aria-live="polite"><strong data-points-value>{number(scenario.points)}</strong><span>points for your team, for this pair</span><small data-factor-values>Sharing {number(scenario.sharing)} × quality {number(scenario.quality)}</small></output>
    </div>
    <p className="explorer-help" id="score-help">{teams===1?"With one credited team, your entry is the reference: D = D₀. Add another team to explore the discriminant factor.":"If your discriminant is D₀ raised to power p, then log D₀ / log D = 1/p. A larger discriminant earns fewer points."}</p>
  </div>;
}
