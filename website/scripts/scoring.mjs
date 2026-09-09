/**
 * Non-baseline illustration with D = D0^power.
 * A sole credited team defines D0, so its effective power is necessarily 1.
 * @param {number} teams
 * @param {number} power
 */
export function pairScenario(teams,power) {
  if(!Number.isInteger(teams)||teams<1||!Number.isFinite(power)||power<1) {
    throw new RangeError("Use a positive integer team count and a discriminant power of at least one.");
  }
  const effectivePower=teams===1?1:power;
  const sharing=2**(1-teams);
  const quality=1/effectivePower;
  return {power:effectivePower,sharing,quality,points:sharing*quality};
}
