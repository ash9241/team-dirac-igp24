labels := [];
stream := InputTextFile("/tmp/current_tc0_labels_20260813.txt");
while true do
  line := ReadLine(stream);
  if line = fail then break; fi;
  Add(labels, Int(Chomp(line)));
od;
CloseStream(stream);
for t in labels do
  g := TransitiveGroup(24, t);
  blocks := AllBlocks(g);
  sizes := Set(List(blocks, Length));
  Print("GROUP|24T", t, "|", Size(g), "|", IsSolvableGroup(g), "|",
        JoinStringsWithSeparator(List(sizes, String), ","), "|",
        StructureDescription(g), "\n");
od;
QUIT_GAP(0);
