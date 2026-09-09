FixedCounts := function(g, degree)
  local reps, out, x, fixed, i;
  reps := List(ConjugacyClasses(g), Representative);
  out := [];
  for x in reps do
    if Order(x) = 1 or Order(x) = 2 then
      fixed := 0;
      for i in [1..degree] do
        if i^x = i then fixed := fixed + 1; fi;
      od;
      AddSet(out, fixed);
    fi;
  od;
  return out;
end;

CartesianAction := function(left, d1, right, d2)
  local gens, g, images, i, j;
  gens := [];
  for g in GeneratorsOfGroup(left) do
    images := [];
    for i in [1..d1] do
      for j in [1..d2] do
        Add(images, (i^g - 1) * d2 + j);
      od;
    od;
    Add(gens, PermList(images));
  od;
  for g in GeneratorsOfGroup(right) do
    images := [];
    for i in [1..d1] do
      for j in [1..d2] do
        Add(images, (i - 1) * d2 + j^g);
      od;
    od;
    Add(gens, PermList(images));
  od;
  return Group(gens);
end;

CensusFamily := function(name, d1, n1, d2, n2)
  local t1, t2, left, right, product, f1, f2, possible, a, b;
  for t1 in [1..n1] do
    left := TransitiveGroup(d1, t1);
    f1 := FixedCounts(left, d1);
    for t2 in [1..n2] do
      right := TransitiveGroup(d2, t2);
      f2 := FixedCounts(right, d2);
      product := CartesianAction(left, d1, right, d2);
      possible := [];
      for a in f1 do
        for b in f2 do AddSet(possible, a*b); od;
      od;
      Print("ROUTE|", name, "|", d1, "T", t1, "|", d2, "T", t2,
            "|24T", TransitiveIdentification(product), "|", Size(product),
            "|", JoinStringsWithSeparator(List(f1, String), ","),
            "|", JoinStringsWithSeparator(List(f2, String), ","),
            "|", JoinStringsWithSeparator(List(possible, String), ","), "\n");
    od;
  od;
end;

CensusFamily("2x12", 2, 1, 12, 301);
CensusFamily("3x8", 3, 2, 8, 50);
CensusFamily("4x6", 4, 5, 6, 16);
QUIT_GAP(0);
