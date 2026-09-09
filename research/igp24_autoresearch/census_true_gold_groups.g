raw := StringFile("true_gold_t.txt");
labels := List(Filtered(SplitString(raw, "\n"), s -> Length(s) > 0), Int);
for t in labels do
  G := TransitiveGroup(24, t);
  Print("24T", t, "\t", Size(G), "\t", IsSolvableGroup(G), "\t",
        IsAbelian(G), "\t", StructureDescription(G), "\n");
od;
