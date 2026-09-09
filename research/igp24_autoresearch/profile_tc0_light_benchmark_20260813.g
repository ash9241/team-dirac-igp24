for t in [18016,22561,24548,1000,24000] do
  start := Runtime();
  g := TransitiveGroup(24, t);
  afterLoad := Runtime();
  order := Size(g);
  afterOrder := Runtime();
  solvable := IsSolvableGroup(g);
  afterSolvable := Runtime();
  blocks := AllBlocks(g);
  afterBlocks := Runtime();
  Print("BENCH|", t, "|load=", afterLoad-start, "|order=", afterOrder-afterLoad,
        "|solvable=", afterSolvable-afterOrder, "|blocks=", afterBlocks-afterSolvable,
        "|size=", order, "|isSolvable=", solvable, "|blockSizes=",
        JoinStringsWithSeparator(List(Set(List(blocks, Length)), String), ","), "\n");
od;
QUIT_GAP(0);
