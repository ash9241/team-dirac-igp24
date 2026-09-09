labels := [];
stream := InputTextFile("tc0_labels.txt");
while true do
  line := ReadLine(stream);
  if line = fail then break; fi;
  Add(labels, Int(Chomp(line)));
od;
CloseStream(stream);
out := OutputTextFile("tc0_groups.out", false);
for t in labels do
  g := TransitiveGroup(24, t);
  blocks := AllBlocks(g);
  sizes := Set(List(blocks, Length));
  AppendTo(out, "GROUP|24T", t, "|", Size(g), "|", IsSolvableGroup(g), "|",
           JoinStringsWithSeparator(List(sizes, String), ","), "\n");
od;
CloseStream(out);
QUIT_GAP(0);
