LoadPackage("transgrp");

input := InputTextFile("data/true_gold_group_orders_20260813.tsv");;
labels := [];;
while not IsEndOfStream(input) do
  line := ReadLine(input);;
  if line <> fail then
    fields := SplitString(Chomp(line), "\t");;
    if Length(fields) >= 3 and Int(fields[2]) = 1536 then
      Add(labels, Int(fields[1]{[4..Length(fields[1])]}));
    fi;
  fi;
od;
CloseStream(input);;

Print("label\torder\tstructure\tblockSize\tblockCount\tkernelOrder\tquotientOrder\tquotientT\tfiberOrder\tfiberT\tfiberStructure\n");
for t in labels do
  g := TransitiveGroup(24, t);;
  found := false;;
  for b in AllBlocks(g) do
    if Length(b) = 8 then
      blocks := Orbit(g, b, OnSets);;
      if Length(blocks) = 3 then
        action := ActionHomomorphism(g, blocks, OnSets);;
        quotient := Image(action);;
        stabilizer := Stabilizer(g, b, OnSets);;
        fiber := Action(stabilizer, b, OnPoints);;
        Print("24T", t,
              "\t", Size(g),
              "\t", StructureDescription(g),
              "\t8\t", Length(blocks),
              "\t", Size(Kernel(action)),
              "\t", Size(quotient),
              "\t", TransitiveIdentification(quotient),
              "\t", Size(fiber),
              "\t", TransitiveIdentification(fiber),
              "\t", StructureDescription(fiber),
              "\n");
        found := true;;
        break;
      fi;
    fi;
  od;
  if not found then
    Print("24T", t, "\t", Size(g), "\t", StructureDescription(g), "\tNA\n");
  fi;
od;
QUIT;
