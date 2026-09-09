if LoadPackage("transgrp") = fail then
  Error("transgrp unavailable");
fi;
for t in [1..NrTransitiveGroups(12)] do
  q := TransitiveGroup(12,t);
  fiber := Group((1,2));
  w := WreathProduct(fiber,q);
  if NrMovedPoints(w) <> 24 or not IsTransitive(w,[1..24]) then
    Error("unexpected wreath action");
  fi;
  Print("MAP|",t,"|",TransitiveIdentification(w),"|",Size(w),"\n");
od;
QUIT;
