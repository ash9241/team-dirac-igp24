F := GF(7);
G := GL(2,7);
vectors := Filtered(AsList(F^2), v -> v <> Zero(F^2));
canonical := function(v)
  local a, b;
  a := List(v, IntFFE);
  b := List(-v, IntFFE);
  if a < b then return a; else return b; fi;
end;
pairs := Set(List(vectors, canonical));
action := Action(G, pairs, function(v, g)
  return canonical(List(v, x -> x * One(F)) * g);
end);
Print("full_order=", Size(G), " image_order=", Size(action),
      " transitive=", IsTransitive(action, [1..24]),
      " id=", TransitiveIdentification(action), "\n");
classes := ConjugacyClassesSubgroups(G);
seen := [];
for class in classes do
  H := Representative(class);
  A := Action(H, pairs, function(v, g)
    return canonical(List(v, x -> x * One(F)) * g);
  end);
  if IsTransitive(A, [1..24]) then
    id := TransitiveIdentification(A);
    key := [id, Size(A)];
    if not key in seen then
      Add(seen, key);
      Print("transitive_id=24T", id, " image_order=", Size(A),
            " subgroup_order=", Size(H), " structure=", StructureDescription(H), "\n");
    fi;
  fi;
od;
Print("conjugacy_classes=", Length(classes), " distinct_transitive_actions=", Length(seen), "\n");
