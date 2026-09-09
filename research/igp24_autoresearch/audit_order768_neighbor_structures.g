LoadPackage("transgrp");
labels := [1958, 1961, 1970, 1975, 1977, 2025, 2030, 2050];;
for t in labels do
  g := TransitiveGroup(24, t);;
  n := FittingSubgroup(g);;
  soc := Socle(g);;
  der := DerivedSubgroup(g);;
  Print("24T", t,
        "\torder=", Size(g),
        "\tstructure=", StructureDescription(g),
        "\tderived=", Size(der),
        "\tab=", AbelianInvariants(g/der),
        "\tfitting=", Size(n), ":", StructureDescription(n),
        "\tsocle=", Size(soc), ":", StructureDescription(soc),
        "\tblocks=", Set(List(AllBlocks(g), Length)),
        "\n");
od;
QUIT;
