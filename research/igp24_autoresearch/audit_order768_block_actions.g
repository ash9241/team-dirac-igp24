LoadPackage("transgrp");

labels := [1958, 1961, 1970, 1975, 1977, 2025, 2030, 2050];;
for t in labels do
  g := TransitiveGroup(24, t);;
  Print("24T", t, "\n");
  for b in AllBlocks(g) do
    size := Length(b);;
    if size in [2, 4, 8, 12] then
      blocks := Orbit(g, b, OnSets);;
      quotient := Image(ActionHomomorphism(g, blocks, OnSets));;
      stabilizer := Stabilizer(g, b, OnSets);;
      fiber := Action(stabilizer, b, OnPoints);;
      Print("  blockSize=", size,
            " count=", Length(blocks),
            " kernelOrder=", Size(Kernel(ActionHomomorphism(g, blocks, OnSets))),
            " quotientOrder=", Size(quotient),
            " quotientT=", TransitiveIdentification(quotient),
            " fiberOrder=", Size(fiber),
            " fiberT=", TransitiveIdentification(fiber),
            " fiberStructure=", StructureDescription(fiber),
            "\n");
    fi;
  od;
od;
QUIT;
