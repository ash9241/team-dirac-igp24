if not IsBound(PROFILE_INPUT) then
  Error("set PROFILE_INPUT before reading this script");
fi;

stream := InputTextFile(PROFILE_INPUT);
while true do
  line := ReadLine(stream);
  if line = fail then
    break;
  fi;
  t := Int(Chomp(line));
  g := TransitiveGroup(24, t);
  blockSizes := Set(List(AllBlocks(g), Length));
  Print(
    "GROUP|24T", t,
    "|", Size(g),
    "|", IsSolvableGroup(g),
    "|", JoinStringsWithSeparator(List(blockSizes, String), ","),
    "\n"
  );
od;
CloseStream(stream);
QUIT_GAP(0);
