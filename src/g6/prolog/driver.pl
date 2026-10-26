% SPDX-License-Identifier: Apache-2.0
%
% G6's fixed driver for SWI-Prolog (g6.prolog). G6 copies this file into a fresh run directory, beside the caller's
% program (program.pl), the caller's goal (goal.pl) and G6's request (request.json); nothing in it is built from
% caller text. It is a module of its own, g6_driver, so a program can neither define nor call the driver's
% predicates, and it never consults the program, so no directive in it runs:
%
%   * the program is read clause by clause with read_term/3, with quasi-quotations switched off (they call a parser
%     at read time). A ':- ...' or '?- ...' term is refused, and so is a clause whose head is not callable, is
%     qualified with a module, or is a control construct, a built-in, a library predicate or a hook SWI-Prolog calls:
%     a program defines only its own predicates;
%   * the restricted syntax: every goal in a clause body, and the goal itself (read as one term), calls a predicate
%     the program defines or one on the allow-list (allowed/1), never a variable and never Module:Goal. The control
%     constructs and the meta predicates it admits (inner/2) have their goal arguments checked the same way, and
%     maplist's closure must name a predicate. A program that keeps to it is asserted into the fresh module
%     g6_program and made static;
%   * library(sandbox)'s safe_goal/1 checks the goal as well, an extra layer. Neither check is a security boundary:
%     SWI-Prolog runs as the person who started it, and a program they do not trust belongs in their own sandbox;
%   * the goal runs under call_with_time_limit/2 and call_with_inference_limit/3, collecting at most max_solutions
%     answers, and the stack limit on swipl's command line ends a search that builds too much;
%   * answers and errors are written with write_term (answer/2 and written/2), quoted, with no portray hook and
%     attributes ignored: no term the caller built goes through message translation, print_message/2 or a portray
%     hook;
%   * each record G6 reads is one JSON line on standard output, after a nonce G6 draws for the run and keeps out of
%     anything the caller's code can reach, so no other output can pass for a record.
%
% Modes (request.json "mode"): "check" (read the program only), "query" (read, check and run the goal) and "recheck"
% (read and check again, then put each answer G6 was given back into the goal and run it once, which must hold with
% the answer as general as it was given; and, when the first search finished, run the search again with a cap one
% higher, which must give the same answers in the same order and no more).

:- module(g6_driver, []).

:- use_module(library(sandbox)).
:- use_module(library(http/json)).
:- use_module(library(time)).
:- use_module(library(lists)).
:- use_module(library(apply)).

:- initialization(main, main).

main :-
    set_prolog_flag(quasi_quotations, false),
    set_stream(user_output, encoding(utf8)),
    read_request(Request),
    get_dict(nonce, Request, Nonce),
    catch(run(Request, Nonce), Error, report_failure(Nonce, Error)),
    emit(Nonce, _{kind: "end"}),
    halt(0).

read_request(Request) :-
    setup_call_cleanup(open('request.json', read, In, [encoding(utf8)]),
                       json_read_dict(In, Request, [value_string_as(string)]),
                       close(In)).

report_failure(Nonce, Error) :-
    error_text(Error, Text),
    emit(Nonce, _{kind: "driver_error", message: Text}).

% --- records -------------------------------------------------------------------------------------------------------

emit(Nonce, Dict) :-
    with_output_to(string(Json), json_write_dict(current_output, Dict, [width(0)])),
    format(user_output, "~n~w ~w~n", [Nonce, Json]),
    flush_output(user_output).

% written(+Term, -Text): Term as quoted text, with no portray hook and attributes ignored, cut off below depth 12.
written(Term, Text) :-
    with_output_to(string(Text),
                   write_term(Term, [quoted(true), portray(false), attributes(ignore), max_depth(12)])).

% error_text(+Error, -Text): an error as text, its terms written by written/2; nothing the caller built goes through
% SWI-Prolog's message translation.
error_text(error(Formal, Context), Text) :- !,
    written(Formal, FormalText),
    (   nonvar(Context),
        Context = context(Where, _),
        nonvar(Where)
    ->  written(Where, WhereText),
        atomics_to_string([FormalText, " in ", WhereText], Text)
    ;   Text = FormalText
    ).
error_text(Error, Text) :-
    written(Error, Text).

% syntax_text(+What, -Text): a syntax error as text, from the reader's own term for it.
syntax_text(What, Text) :-
    (   atom(What)
    ->  atomic_list_concat(Words, '_', What),
        atomic_list_concat(Words, ' ', Said)
    ;   written(What, Said)
    ),
    atomics_to_string(["Syntax error: ", Said], Text).

problem(Nonce, Stage, Problem, Line, Column, Text) :-
    emit(Nonce, _{kind: "problem", stage: Stage, problem: Problem, line: Line, column: Column, message: Text}).

% --- the run -------------------------------------------------------------------------------------------------------

run(Request, Nonce) :-
    current_prolog_flag(version, V),
    Major is V // 10000, Minor is (V // 100) mod 100, Patch is V mod 100,
    format(string(Version), "~w.~w.~w", [Major, Minor, Patch]),
    emit(Nonce, _{kind: "version", version: Version}),
    Module = g6_program,
    set_module(Module:class(user)),          % the fresh module exists even when the program is empty
    load_program('program.pl', Module, Nonce, Defined, Problems),
    get_dict(mode, Request, Mode),
    (   Mode == "check"
    ->  true
    ;   Problems > 0
    ->  true
    ;   read_goal('goal.pl', Defined, Goal, Bindings, Nonce)
    ->  run_goal(Mode, Request, Module, Goal, Bindings, Nonce)
    ;   true
    ).

% --- the program ---------------------------------------------------------------------------------------------------

% load_program(+File, +Module, +Nonce, -Defined, -Problems): read File; only when every clause is admitted and keeps
% to the restricted syntax is it asserted into Module, its predicates made static. Defined lists the predicates the
% admitted clauses define, def(Name/Arity, FirstLine, Clauses); Problems counts what was reported.
load_program(File, Module, Nonce, Defined, Problems) :-
    setup_call_cleanup(open(File, read, In, [encoding(utf8)]),
                       read_clauses(In, Module, Nonce, Admitted, 0, Read),
                       close(In)),
    foldl(note_clause, Admitted, [], Noted),
    reverse(Noted, Defined),
    foldl(check_clause(Defined, Nonce), Admitted, Read, Checked),
    (   Checked =:= 0
    ->  foldl(assert_clause(Module, Nonce), Admitted, 0, Problems),
        (   Problems =:= 0
        ->  maplist(make_static(Module), Defined)
        ;   true
        )
    ;   Problems = Checked
    ),
    maplist(report_predicate(Nonce), Defined).

read_clauses(In, Module, Nonce, Admitted, Problems0, Problems) :-
    catch(read_term(In, Term, [module(Module), term_position(Pos)]), Error, true),
    (   nonvar(Error)
    ->  syntax_problem(Error, Nonce, "program"),
        Problems1 is Problems0 + 1,
        read_clauses(In, Module, Nonce, Admitted, Problems1, Problems)
    ;   Term == end_of_file
    ->  Admitted = [],
        Problems = Problems0
    ;   stream_position_data(line_count, Pos, Line),
        stream_position_data(line_position, Pos, Column),
        classify(Term, Verdict),
        (   Verdict = admitted(Clause)
        ->  Admitted = [clause(Clause, Line, Column)|Rest],
            Problems1 = Problems0
        ;   Verdict = refused(Problem, Text),
            problem(Nonce, "program", Problem, Line, Column, Text),
            Admitted = Rest,
            Problems1 is Problems0 + 1
        ),
        read_clauses(In, Module, Nonce, Rest, Problems1, Problems)
    ).

% classify(+Term, -Verdict): admitted(Clause), a clause the program may have, or refused(Problem, Text).
classify(Term, refused("clause", "a clause is a callable term, not a variable")) :- var(Term), !.
classify((:- _), refused("directive", Text)) :- !, directive_text(Text).
classify((?- _), refused("directive", Text)) :- !, directive_text(Text).
classify((Head --> Body), Verdict) :- !,
    (   catch(dcg_translate_rule((Head --> Body), Clause), _, fail)
    ->  classify(Clause, Verdict)
    ;   Verdict = refused("clause", "the grammar rule does not translate to a clause")
    ).
classify((Head :- Body), Verdict) :- !,
    head_verdict(Head, (Head :- Body), Verdict).
classify(Head, Verdict) :-
    head_verdict(Head, Head, Verdict).

directive_text("a directive (:- ... or ?- ...) is refused, never run: G6 reads the program, it does not consult it").

head_verdict(Head, Clause, Verdict) :-
    (   head_refusal(Head, Text)
    ->  Verdict = refused("clause", Text)
    ;   Verdict = admitted(Clause)
    ).

% head_refusal(+Head, -Text): why a program may not define Head; fails when it may. A program defines only its own
% predicates: never a control construct, a built-in, a library predicate or a hook SWI-Prolog calls by name. It runs
% while g6_program is still empty, so "visible" there means system, library or user.
head_refusal(Head, Text) :-
    \+ callable(Head), !,
    written(Head, Written),
    atomics_to_string(["the clause's head ", Written, " is not callable"], Text).
head_refusal(Module:Head, Text) :- !,
    indicator(Module:Head, PI),
    atomics_to_string(["the clause for ", PI, " is refused: its head is qualified with a module, and a program ",
                       "defines only its own predicates"], Text).
head_refusal(Head, Text) :-
    functor(Head, Name, Arity),
    (   control(Head)
    ->  Kind = "a control construct"
    ;   sub_atom(Name, 0, _, _, '$')
    ->  Kind = "a system name"
    ;   hook(Name/Arity)
    ->  Kind = "a hook SWI-Prolog calls"
    ;   predicate_property(system:Head, built_in)
    ->  Kind = "a built-in predicate"
    ;   predicate_property(g6_program:Head, visible)
    ->  Kind = "a built-in or library predicate"
    ),
    indicator(Head, PI),
    atomics_to_string(["the clause for ", PI, " is refused: ", PI, " is ", Kind,
                       ", and a program defines only its own predicates"], Text).

control((_,_)).
control((_;_)).
control((_->_)).
control((_*->_)).
control(\+(_)).
control((_:-_)).
control((_-->_)).
control((_|_)).
control(!).

% hook(Name/Arity): predicates SWI-Prolog calls by name, in user or in any module, which a program may not define.
hook(portray/1).
hook(message_hook/3).
hook(message_property/2).
hook(exception/3).
hook(prolog_exception_hook/4).
hook(prolog_exception_hook/5).
hook(term_expansion/2).
hook(term_expansion/4).
hook(goal_expansion/2).
hook(goal_expansion/4).
hook(attr_unify_hook/2).
hook(attr_portray_hook/2).
hook(attribute_goals/3).
hook(prolog_load_file/2).

% indicator(+Goal, -Text): Goal's predicate indicator, Name/Arity or Module:Name/Arity, as text written part by part
% (whole, SWI-Prolog would bracket an operator name).
indicator(Module:Goal, Text) :- !,
    written(Module, ModuleText),
    (   callable(Goal)
    ->  indicator(Goal, GoalText)
    ;   written(Goal, GoalText)
    ),
    atomics_to_string([ModuleText, ":", GoalText], Text).
indicator(Goal, Text) :-
    functor(Goal, Name, Arity),
    written(Name, NameText),
    atomics_to_string([NameText, "/", Arity], Text).

clause_head((Head :- _), Head) :- !.
clause_head(Head, Head).

note_clause(clause(Clause, Line, _), Defined0, Defined) :-
    clause_head(Clause, Head),
    functor(Head, Name, Arity),
    note_defined(Name/Arity, Line, Defined0, Defined).

note_defined(PI, Line, Defined0, Defined) :-
    (   selectchk(def(PI, First, Count0), Defined0, Rest)
    ->  Count is Count0 + 1,
        Defined = [def(PI, First, Count)|Rest]
    ;   Defined = [def(PI, Line, 1)|Defined0]
    ).

% check_clause(+Defined, +Nonce, +Clause, +Problems0, -Problems): report the first goal in an admitted clause's body
% that is outside the restricted syntax.
check_clause(Defined, Nonce, clause(Clause, Line, Column), Problems0, Problems) :-
    (   Clause = (Head :- Body),
        outside(Body, Defined, Problem, Calls)
    ->  indicator(Head, PI),
        atomics_to_string(["the clause for ", PI, " ", Calls], Text),
        problem(Nonce, "program", Problem, Line, Column, Text),
        Problems is Problems0 + 1
    ;   Problems = Problems0
    ).

assert_clause(Module, Nonce, clause(Clause, Line, Column), Problems0, Problems) :-
    catch(assertz(Module:Clause), Error, true),
    (   var(Error)
    ->  Problems = Problems0
    ;   error_text(Error, Text),
        problem(Nonce, "program", "clause", Line, Column, Text),
        Problems is Problems0 + 1
    ).

make_static(Module, def(Name/Arity, _, _)) :-
    compile_predicates([Module:Name/Arity]).

report_predicate(Nonce, def(Name/Arity, Line, Count)) :-
    atom_string(Name, NameText),
    emit(Nonce, _{kind: "predicate", name: NameText, arity: Arity, clauses: Count, line: Line}).

syntax_problem(error(syntax_error(What), Where), Nonce, Stage) :- !,
    error_place(Where, Line, Column),
    syntax_text(What, Text),
    problem(Nonce, Stage, "syntax", Line, Column, Text).
syntax_problem(Error, Nonce, Stage) :-
    error_text(Error, Text),
    problem(Nonce, Stage, "syntax", 0, 0, Text).

error_place(file(_, Line, Column, _), Line, Column) :- !.
error_place(stream(_, Line, Column, _), Line, Column) :- !.
error_place(_, 0, 0).

% --- the restricted syntax -----------------------------------------------------------------------------------------

% allowed(Name/Arity): the allow-list, what a goal may call besides the program's own predicates, the control
% constructs and the meta predicates of inner/2. tests/prolog/test_prolog_block.py pins it.
allowed(true/0).
allowed(fail/0).
allowed(false/0).
allowed(!/0).
allowed((=)/2).
allowed((\=)/2).
allowed((==)/2).
allowed((\==)/2).
allowed((@<)/2).
allowed((@>)/2).
allowed((@=<)/2).
allowed((@>=)/2).
allowed(compare/3).
allowed((is)/2).
allowed((=:=)/2).
allowed((=\=)/2).
allowed((<)/2).
allowed((>)/2).
allowed((=<)/2).
allowed((>=)/2).
allowed(between/3).
allowed(succ/2).
allowed(plus/3).
allowed(var/1).
allowed(nonvar/1).
allowed(atom/1).
allowed(number/1).
allowed(integer/1).
allowed(float/1).
allowed(atomic/1).
allowed(compound/1).
allowed(callable/1).
allowed(is_list/1).
allowed(ground/1).
allowed(functor/3).
allowed(arg/3).
allowed((=..)/2).
allowed(copy_term/2).
allowed(atom_codes/2).
allowed(atom_chars/2).
allowed(char_code/2).
allowed(atom_number/2).
allowed(number_codes/2).
allowed(number_chars/2).
allowed(atom_length/2).
allowed(member/2).
allowed(memberchk/2).
allowed(append/3).
allowed(length/2).
allowed(nth0/3).
allowed(nth1/3).
allowed(last/2).
allowed(reverse/2).
allowed(msort/2).
allowed(sort/2).
allowed(sort/4).
allowed(sum_list/2).
allowed(max_list/2).
allowed(min_list/2).
allowed(numlist/3).
allowed(list_to_set/2).

% inner(+Goal, -Goals): the goals a control construct or an admitted meta predicate calls, checked like any goal.
inner((A, B), [A, B]).
inner((A ; B), [A, B]).
inner((A -> B), [A, B]).
inner((A *-> B), [A, B]).
inner(\+ A, [A]).
inner(findall(_, Goal, _), [Goal]).
inner(forall(Condition, Action), [Condition, Action]).
inner(aggregate_all(_, Goal, _), [Goal]).
inner(bagof(_, Goal, _), [Free]) :- free_goal(Goal, Free).
inner(setof(_, Goal, _), [Free]) :- free_goal(Goal, Free).
inner(Goal, [Called]) :- maplist_goal(Goal, Closure, Extra), extended(Closure, Extra, Called).

% free_goal(+Goal, -Free): bagof/3 and setof/3's goal with its Var^ prefixes taken off.
free_goal(Goal, Goal) :- var(Goal), !.
free_goal(_^Goal, Free) :- !, free_goal(Goal, Free).
free_goal(Goal, Goal).

maplist_goal(maplist(Closure, _), Closure, 1).
maplist_goal(maplist(Closure, _, _), Closure, 2).
maplist_goal(maplist(Closure, _, _, _), Closure, 3).

% extended(+Closure, +Extra, -Goal): the goal maplist calls, Closure with Extra more arguments.
extended(Closure, Extra, Goal) :-
    Closure =.. [Name|Arguments],
    length(More, Extra),
    append(Arguments, More, All),
    Goal =.. [Name|All].

closure(Closure) :- callable(Closure), Closure \= _:_.

aggregate_spec(Spec) :- nonvar(Spec), aggregate_form(Spec).

aggregate_form(count).
aggregate_form(sum(_)).
aggregate_form(max(_)).
aggregate_form(min(_)).
aggregate_form(bag(_)).
aggregate_form(set(_)).
aggregate_form(max(_, _)).
aggregate_form(min(_, _)).

% outside(+Goal, +Defined, -Problem, -Calls): the first part of Goal outside the restricted syntax, Problem being
% "not_allowed" or "undefined" and Calls saying what it calls; fails when Goal keeps to it.
outside(Goal, _, "not_allowed", "calls a variable as a goal, which the restricted syntax does not allow") :-
    var(Goal), !.
outside(Module:Goal, _, "not_allowed", Calls) :- !,
    indicator(Module:Goal, PI),
    atomics_to_string(["calls the module-qualified goal ", PI, ", which the restricted syntax does not allow"],
                      Calls).
outside(aggregate_all(Spec, _, _), _, "not_allowed", Calls) :-
    \+ aggregate_spec(Spec), !,
    atomics_to_string(["calls aggregate_all/3 with a first argument other than count, sum(E), max(E), min(E), ",
                       "bag(E), set(E), max(E, W) or min(E, W)"], Calls).
outside(Goal, _, "not_allowed", Calls) :-
    maplist_goal(Goal, Closure, _),
    \+ closure(Closure), !,
    indicator(Goal, PI),
    atomics_to_string(["calls ", PI, " with a closure that does not name a predicate (a variable, a number or ",
                       "Module:Closure), which the restricted syntax does not allow"], Calls).
outside(Goal, Defined, Problem, Calls) :-
    inner(Goal, Goals), !,
    member(Inner, Goals),
    outside(Inner, Defined, Problem, Calls), !.
outside(Goal, _, "not_allowed", Calls) :-
    \+ callable(Goal), !,
    written(Goal, Written),
    atomics_to_string(["calls ", Written, ", which is not a goal"], Calls).
outside(Goal, Defined, Problem, Calls) :-
    functor(Goal, Name, Arity),
    \+ memberchk(def(Name/Arity, _, _), Defined),
    \+ allowed(Name/Arity),
    indicator(Goal, PI),
    (   predicate_property(g6_program:Goal, visible)
    ->  Problem = "not_allowed",
        atomics_to_string(["calls ", PI, ", a built-in or library predicate the restricted syntax does not allow"],
                          Calls)
    ;   Problem = "undefined",
        atomics_to_string(["calls ", PI, ", which the program does not define"], Calls)
    ).

% --- the goal ------------------------------------------------------------------------------------------------------

read_goal(File, Defined, Goal, Bindings, Nonce) :-
    read_file_to_string(File, Text, [encoding(utf8)]),
    one_term(Text, Goal0, Bindings0, Problem),
    (   nonvar(Problem)
    ->  goal_problem(Problem, Nonce),
        fail
    ;   \+ callable(Goal0)
    ->  goal_problem("the goal is not a callable term", Nonce),
        fail
    ;   outside(Goal0, Defined, Kind, Calls)
    ->  atomics_to_string(["the goal ", Calls], Message),
        problem(Nonce, "goal", Kind, 0, 0, Message),
        fail
    ;   Goal = Goal0,
        Bindings = Bindings0
    ).

goal_problem(Problem, Nonce) :-
    (   Problem = error(_, _)
    ->  syntax_problem(Problem, Nonce, "goal")
    ;   problem(Nonce, "goal", "syntax", 0, 0, Problem)
    ).

% one_term(+Text, -Term, -Bindings, -Problem): Text holds exactly one term, with or without its full stop; Problem
% stays unbound when it does.
one_term(Text, Term, Bindings, Problem) :-
    read_one(Text, Term0, Bindings0, Problem0),
    (   nonvar(Problem0),
        Problem0 = error(syntax_error(end_of_file), _)
    ->  string_concat(Text, "\n.", Stopped),
        read_one(Stopped, Term, Bindings, Problem)
    ;   Term = Term0,
        Bindings = Bindings0,
        Problem = Problem0
    ).

read_one(Text, Term, Bindings, Problem) :-
    setup_call_cleanup(open_string(Text, In),
                       read_one_from(In, Term, Bindings, Problem),
                       close(In)).

read_one_from(In, Term, Bindings, Problem) :-
    catch(read_term(In, Term0, [module(g6_program), variable_names(Bindings0)]), Error, true),
    (   nonvar(Error)
    ->  Problem = Error
    ;   Term0 == end_of_file
    ->  Problem = "the goal is empty"
    ;   catch(read_term(In, Next, [module(g6_program)]), Error2, true),
        (   nonvar(Error2)
        ->  Problem = "there is text after the term's full stop: give exactly one term"
        ;   Next == end_of_file
        ->  Term = Term0,
            Bindings = Bindings0
        ;   Problem = "the text holds more than one term: give exactly one term"
        )
    ).

run_goal("query", Request, Module, Goal, Bindings, Nonce) :-
    get_dict(max_solutions, Request, Max),
    get_dict(time_limit, Request, Seconds),
    get_dict(inference_limit, Request, Inferences),
    (   sandbox_allows(Module, Goal, Nonce)
    ->  search(Module, Goal, Bindings, Max, Seconds, Inferences, Nonce)
    ;   true
    ).
run_goal("recheck", Request, Module, Goal, Bindings, Nonce) :-
    get_dict(answers, Request, Answers),
    get_dict(exhausted, Request, Exhausted),
    get_dict(time_limit, Request, Seconds),
    get_dict(inference_limit, Request, Inferences),
    catch(call_with_time_limit(Seconds,
                               recheck(Answers, Exhausted, Module, Goal, Bindings, Inferences, Nonce)),
          time_limit_exceeded,
          emit(Nonce, _{kind: "stopped", by: "time_limit"})).

% sandbox_allows(+Module, +Goal, +Nonce): library(sandbox)'s safe_goal/1, an extra layer after the restricted syntax.
sandbox_allows(Module, Goal, Nonce) :-
    catch(safe_goal(Module:Goal), Error, true),
    (   var(Error)
    ->  true
    ;   error_text(Error, Text),
        culprit(Error, Culprit),
        (   Error = error(Formal, _), compound(Formal)
        ->  functor(Formal, Kind, _)
        ;   Kind = other
        ),
        atom_string(Kind, KindText),
        emit(Nonce, _{kind: "unsafe", message: Text, culprit: Culprit, error: KindText}),
        fail
    ).

culprit(error(permission_error(_, _, What), _), Text) :- !, written(What, Text).
culprit(error(existence_error(_, What), _), Text) :- !, written(What, Text).
culprit(error(Formal, _), Text) :- !, written(Formal, Text).
culprit(Error, Text) :- written(Error, Text).

% search: run the goal, collecting at most Max answers, within the time, inference and stack limits.
search(Module, Goal, Bindings, Max, Seconds, Inferences, Nonce) :-
    State = state(0, [], exhausted),
    catch(call_with_time_limit(Seconds,
                               call_with_inference_limit(collect(Module, Goal, Bindings, Max, State),
                                                         Inferences, Outcome)),
          Error, true),
    arg(2, State, Reversed),
    reverse(Reversed, Answers),
    forall(member(answer(Pairs, Text), Answers),
           emit(Nonce, _{kind: "answer", bindings: Pairs, text: Text})),
    (   nonvar(Error)
    ->  (   Error == time_limit_exceeded
        ->  emit(Nonce, _{kind: "stopped", by: "time_limit"})
        ;   Error = error(resource_error(_), _)
        ->  emit(Nonce, _{kind: "stopped", by: "memory_limit"})
        ;   Error == g6_cyclic_answer
        ->  emit(Nonce, _{kind: "goal_error",
                          message: "an answer is a cyclic term, which G6 cannot write down and re-check"})
        ;   Error == g6_constrained_answer
        ->  emit(Nonce, _{kind: "goal_error",
                          message: "an answer carries constraints on its variables, which G6 cannot re-check"})
        ;   error_text(Error, ErrorText),
            emit(Nonce, _{kind: "goal_error", message: ErrorText})
        )
    ;   Outcome == inference_limit_exceeded
    ->  emit(Nonce, _{kind: "stopped", by: "inference_limit"})
    ;   arg(3, State, How),
        (   How == exhausted
        ->  emit(Nonce, _{kind: "done", exhausted: true})
        ;   emit(Nonce, _{kind: "stopped", by: "max_solutions"})
        )
    ).

collect(Module, Goal, Bindings, Max, State) :-
    (   Module:Goal,
        answer(Bindings, Answer),
        arg(1, State, Count0),
        Count is Count0 + 1,
        nb_setarg(1, State, Count),
        arg(2, State, Answers0),
        nb_setarg(2, State, [Answer|Answers0]),
        Count >= Max
    ->  nb_setarg(3, State, max_solutions)
    ;   true
    ).

% answer(+Bindings, -answer(Pairs, Text)): each named variable's value, and the whole answer as one term, written so
% that reading it back keeps the variables its values share. An answer with constraints on its variables is refused.
answer(Bindings, answer(Pairs, Text)) :-
    (   acyclic_term(Bindings)
    ->  true
    ;   throw(g6_cyclic_answer)
    ),
    (   term_attvars(Bindings, [])
    ->  true
    ;   throw(g6_constrained_answer)
    ),
    term_variables(Bindings, Free),
    name_free(Free, 1, Names),
    Options = [quoted(true), variable_names(Names), portray(false), attributes(ignore), max_depth(0)],
    maplist(binding_pair(Options), Bindings, Pairs),
    with_output_to(string(Text), write_term(Bindings, Options)).

name_free([], _, []).
name_free([V|Vs], N, [Name=V|Names]) :-
    format(atom(Name), "_V~w", [N]),
    N1 is N + 1,
    name_free(Vs, N1, Names).

binding_pair(Options, Name=Value, [NameText, ValueText]) :-
    atom_string(Name, NameText),
    with_output_to(string(ValueText), write_term(Value, Options)).

% recheck: each answer, put back into the goal, must hold again as given; when the first search finished, the search
% run again with a cap one higher must give the same answers in the same order and no more (index -1).
recheck(Answers, Exhausted, Module, Goal, Bindings, Inferences, Nonce) :-
    forall(nth0(Index, Answers, Text),
           recheck_one(Index, Text, Module, Goal, Bindings, Inferences, Nonce)),
    (   Exhausted == true
    ->  recheck_end(Answers, Module, Goal, Bindings, Inferences, Nonce)
    ;   true
    ).

recheck_one(Index, Text, Module, Goal, Bindings, Inferences, Nonce) :-
    copy_term(Goal-Bindings, Goal1-Bindings1),
    one_term(Text, Answer, _, Problem),
    (   nonvar(Problem)
    ->  Verdict = "disagreed",
        Why = "the answer could not be read back as one term"
    ;   bind_answer(Answer, Bindings1)
    ->  instantiated(Module, Goal1, Bindings1, Inferences, Verdict, Why)
    ;   Verdict = "disagreed",
        Why = "the answer does not bind exactly the goal's named variables"
    ),
    emit(Nonce, _{kind: "recheck", index: Index, verdict: Verdict, why: Why}).

% instantiated(+Module, +Goal, +Values, +Inferences, -Verdict, -Why): Goal, already instantiated with one answer
% (Values, the goal's named variables and their values), run once. It must hold with the answer as given: Values a
% variant of the snapshot taken before the run, so an answer's free variables stay free.
instantiated(Module, Goal, Values, Inferences, Verdict, Why) :-
    copy_term(Values, Snapshot),
    (   catch(safe_goal(Module:Goal), _, fail)
    ->  (   catch(call_with_inference_limit(once((Module:Goal, Values =@= Snapshot)), Inferences, Outcome),
                  Error, true)
        ->  (   nonvar(Error)
            ->  recheck_error(Error, Verdict, Why)
            ;   Outcome == inference_limit_exceeded
            ->  Verdict = "unfinished",
                Why = "the inference limit stopped the re-check"
            ;   Verdict = "agreed",
                Why = "the goal instantiated with the answer succeeded again, the answer as general as given"
            )
        ;   Verdict = "disagreed",
            Why = "the goal instantiated with the answer failed, or held only by binding the answer's free variables"
        )
    ;   Verdict = "disagreed",
        Why = "SWI-Prolog's sandbox library refused the goal instantiated with the answer"
    ).

% recheck_end(+Answers, +Module, +Goal, +Bindings, +Inferences, +Nonce): the first search said it finished with these
% answers; run it again with a cap one higher.
recheck_end(Answers, Module, Goal, Bindings, Inferences, Nonce) :-
    length(Answers, Count),
    Cap is Count + 1,
    (   catch(safe_goal(Module:Goal), _, fail)
    ->  State = state(0, [], exhausted),
        catch(call_with_inference_limit(collect(Module, Goal, Bindings, Cap, State), Inferences, Outcome),
              Error, true),
        arg(2, State, Reversed),
        reverse(Reversed, Found),
        findall(Text, member(answer(_, Text), Found), Texts),
        length(Texts, Again),
        (   nonvar(Error)
        ->  recheck_error(Error, Verdict, Why)
        ;   Outcome == inference_limit_exceeded
        ->  Verdict = "unfinished",
            Why = "the inference limit stopped the re-check"
        ;   Texts == Answers
        ->  Verdict = "agreed",
            (   Count =:= 0
            ->  Why = "the goal failed again"
            ;   Why = "the search, run again, gave the same answers in the same order and no more"
            )
        ;   Again > Count
        ->  Verdict = "disagreed",
            Why = "the search, run again, found more answers than the first run reported, so it had not finished"
        ;   Verdict = "disagreed",
            Why = "the search, run again, did not give the same answers in the same order"
        )
    ;   Verdict = "disagreed",
        Why = "SWI-Prolog's sandbox library refused the goal on the re-check"
    ),
    emit(Nonce, _{kind: "recheck", index: -1, verdict: Verdict, why: Why}).

% recheck_error(+Error, -Verdict, -Why): a re-check that raised Error. The time limit goes on to run_goal/6.
recheck_error(time_limit_exceeded, _, _) :- !,
    throw(time_limit_exceeded).
recheck_error(error(resource_error(_), _), "unfinished", "the stack limit stopped the re-check") :- !.
recheck_error(Error, "error", Why) :-
    error_text(Error, Why).

bind_answer(Answer, Bindings) :-
    is_list(Answer),
    length(Answer, N),
    length(Bindings, N),
    maplist(bind_named(Bindings), Answer).

bind_named(Bindings, Pair) :-
    nonvar(Pair),
    Pair = (Name = Value),
    atom(Name),
    memberchk(Name = Var, Bindings),
    Var = Value.
