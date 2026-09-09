SetInfoLevel(InfoWarning, 0);

Lift12To24 := function(g)
  local images, i, j;
  images := [];
  for i in [1..12] do
    j := i ^ g;
    Add(images, 2*j-1);
    Add(images, 2*j);
  od;
  return PermList(images);
end;

flips := List([1..12], i -> (2*i-1,2*i));
evenFlips := List([2..12], i -> flips[1] * flips[i]);

for t in [1..301] do
  g := TransitiveGroup(12, t);
  gens := GeneratorsOfGroup(g);
  lifted := ShallowCopy(evenFlips);
  for x in gens do
    y := Lift12To24(x);
    if SignPerm(x) = -1 then
      y := y * flips[1];
    fi;
    Add(lifted, y);
  od;
  h := Group(lifted);
  id := TransitiveIdentification(h);
  Print(t, "|", Size(g), "|", Size(h), "|", id, "\n");
od;
QUIT;
