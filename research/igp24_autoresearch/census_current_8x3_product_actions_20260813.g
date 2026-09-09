SetInfoLevel(InfoWarning, 0);

ProductAction := function(g8, g3)
  local generators, a, b, p, images, image;
  generators := [];
  for a in GeneratorsOfGroup(g8) do
    images := [];
    for p in [1..24] do
      b := QuoInt(p - 1, 8) + 1;
      image := (b - 1) * 8 + (p - (b - 1) * 8) ^ a;
      Add(images, image);
    od;
    Add(generators, PermList(images));
  od;
  for a in GeneratorsOfGroup(g3) do
    images := [];
    for p in [1..24] do
      b := QuoInt(p - 1, 8) + 1;
      image := (b ^ a - 1) * 8 + (p - (b - 1) * 8);
      Add(images, image);
    od;
    Add(generators, PermList(images));
  od;
  return Group(generators);
end;

for a in [1..NrTransitiveGroups(8)] do
  for b in [1..NrTransitiveGroups(3)] do
    g8 := TransitiveGroup(8, a);
    g3 := TransitiveGroup(3, b);
    product := ProductAction(g8, g3);
    Print("8T", a, "|3T", b, "|24T", TransitiveIdentification(product),
          "|", Size(product), "\n");
  od;
od;
QUIT;
