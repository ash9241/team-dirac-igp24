import { pairScenario } from "./scoring.mjs";

const teamsInput = document.querySelector("#credited-teams");
const powerInput = document.querySelector("#discriminant-power");
const format = value => value.toLocaleString("en-US", { maximumFractionDigits: 5 });

if (teamsInput && powerInput) {
  const update = () => {
    const teams = Number(teamsInput.value);
    const result = pairScenario(teams, Number(powerInput.value));
    powerInput.value = String(result.power);
    powerInput.disabled = teams === 1;
    document.querySelector("[data-teams-value]").textContent = String(teams);
    document.querySelector("[data-power-value]").textContent = result.power === 1 ? "" : String(result.power);
    document.querySelector("[data-points-value]").textContent = format(result.points);
    document.querySelector("[data-factor-values]").textContent = "Sharing " + format(result.sharing) + " × quality " + format(result.quality);
    powerInput.setAttribute("aria-valuetext", result.power === 1 ? "Matches the best discriminant" : "Reference discriminant raised to power " + result.power);
    document.querySelector("#score-help").textContent = teams === 1
      ? "With one credited team, your entry is the reference: D = D₀. Add another team to explore the discriminant factor."
      : "If your discriminant is D₀ raised to power p, then log D₀ / log D = 1/p. A larger discriminant earns fewer points.";
  };
  teamsInput.addEventListener("input", update);
  powerInput.addEventListener("input", update);
  update();
}
