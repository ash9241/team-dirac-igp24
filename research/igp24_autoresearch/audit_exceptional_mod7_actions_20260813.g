if LoadPackage("transgrp")=fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);;
f:=GF(7);;
gl:=GL(2,7);;
elements:=Elements(f);;
vectors:=Filtered(Cartesian(elements,elements),v->v<>[Zero(f),Zero(f)]);;
seen:=[];; pairs:=[];;
for v in vectors do
  neg:=-v;;
  key:=Set([v,neg]);;
  if not key in seen then Add(seen,key); Add(pairs,key); fi;
od;
perms:=List(GeneratorsOfGroup(gl),m->PermList(List(pairs,p->
  Position(pairs,Set([p[1]*m,-p[1]*m])))));;
g:=Group(perms);;
if Size(g)<>1008 or not IsTransitive(g) then Error("GL2(7)/+-I action mismatch"); fi;
classes:=ConjugacyClassesSubgroups(g);;
count:=0;; trans:=0;; labels:=[];;
for ci in [1..Length(classes)] do
  h:=Representative(classes[ci]);;
  count:=count+1;;
  if IsTransitive(h,[1..24]) then
    trans:=trans+1;;
    t:=TransitiveIdentification(h);;
    # Every elliptic complex conjugation fixes six sign-pairs.  Retain only
    # subgroup classes containing an involution with that exact action.
    cc:=ConjugacyClasses(h);;
    good:=Filtered(cc,c->Order(Representative(c))=2 and Number([1..24],i->i^Representative(c)=i)=6);;
    if Length(good)>0 then
      AddSet(labels,t);;
      Print("ACTION|",ci,"|",Size(h),"|",t,"|",Length(good),"|",
            StructureDescription(h),"\n");
    fi;
  fi;
od;
Print("COMPLETE|",count,"|",trans,"|",Length(labels),"|",
      JoinStringsWithSeparator(List(labels,String),","),"\n");
QUIT;
