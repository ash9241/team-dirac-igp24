if LoadPackage("transgrp") = fail then
  Error("transgrp unavailable");
fi;
for ft in [1..NrTransitiveGroups(4)] do
  for qt in [1..NrTransitiveGroups(6)] do
    fiber := TransitiveGroup(4,ft);
    quotient := TransitiveGroup(6,qt);
    w := WreathProduct(fiber,quotient);
    if NrMovedPoints(w) <> 24 or not IsTransitive(w,[1..24]) then
      Error("unexpected wreath action");
    fi;
    Print("MAP|",ft,"|",qt,"|",TransitiveIdentification(w),"|",Size(w),"\n");
  od;
od;
QUIT;
