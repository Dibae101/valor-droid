/*
 * Reports, per activity, what each one reads out of the Intent that started it.
 *
 * Why this exists
 * ---------------
 * Launching an activity directly is the cheapest way to reach code the GUI
 * cannot walk to, but launching it *bare* is worse than not launching it at all.
 * Measured on Chess: enabling blind forced activation moved coverage from 28.70%
 * to 5.93%, because an activity whose onCreate immediately dereferences a
 * missing extra dies or renders an empty shell, and exploration then spends its
 * budget on the wreckage.
 *
 * The fix, following Delm (arXiv:2404.19307), is to mock the activity's context
 * rather than only its entry point: supply the extras and data URI it actually
 * reads, so it initialises the way it would have if the user had arrived
 * normally. This tool provides the "what does it read" half. It is a read-only
 * analysis: it never rewrites the APK, so the measured binary stays the shipped
 * binary.
 *
 * Output is JSON on stdout:
 *   {"activities": {"<component>": {"extras": [{"key": "...", "type": "..."}],
 *                                    "reads_data_uri": true}}}
 */
import soot.*;
import soot.jimple.*;
import soot.options.Options;

import java.util.*;

public final class IntentContextAnalyzer {

    /** Intent getters we can satisfy from the command line via `am start`. */
    private static final Map<String, String> EXTRA_GETTERS = new HashMap<>();
    static {
        EXTRA_GETTERS.put("getStringExtra", "string");
        EXTRA_GETTERS.put("getIntExtra", "int");
        EXTRA_GETTERS.put("getLongExtra", "long");
        EXTRA_GETTERS.put("getBooleanExtra", "boolean");
        EXTRA_GETTERS.put("getFloatExtra", "float");
        EXTRA_GETTERS.put("getDoubleExtra", "float");
        // Bundle-backed reads use the same key convention and are commonly
        // satisfied by a string, which is the widest safe guess.
        EXTRA_GETTERS.put("getCharSequenceExtra", "string");
    }

    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("usage: IntentContextAnalyzer <apk> <android-platforms>");
            System.exit(2);
        }
        String apk = args[0];
        String platforms = args[1];

        G.reset();
        Options.v().set_src_prec(Options.src_prec_apk);
        Options.v().set_process_dir(Collections.singletonList(apk));
        Options.v().set_android_jars(platforms);
        // Every APK in this dataset is multi-dex, and Soot reads only
        // classes.dex unless told otherwise. Without this the analysis loaded
        // 3,324 of Chess's 3,858 classes and found four activities, all of them
        // AndroidX base classes -- ChessPreferences, GamesListActivity,
        // PracticeActivity, PuzzleActivity and sixteen more live in classes2.dex
        // through classes12.dex. Those base classes read nothing with a literal
        // key, so the report came back "{}" for every app in the campaign and
        // every activity kept being launched with no context at all.
        Options.v().set_process_multiple_dex(true);
        Options.v().set_whole_program(false);
        Options.v().set_allow_phantom_refs(true);
        Options.v().set_output_format(Options.output_format_none);
        Options.v().set_wrong_staticness(Options.wrong_staticness_ignore);
        Options.v().set_ignore_resolution_errors(true);
        Options.v().set_keep_line_number(false);
        Scene.v().loadNecessaryClasses();

        // activity -> ordered unique extras, plus whether it reads a data URI
        Map<String, LinkedHashMap<String, String>> extras = new TreeMap<>();
        Map<String, Boolean> readsData = new TreeMap<>();

        for (SootClass sc : new ArrayList<>(Scene.v().getApplicationClasses())) {
            if (!isActivity(sc)) {
                continue;
            }
            String name = sc.getName();
            LinkedHashMap<String, String> found = new LinkedHashMap<>();
            boolean data = false;
            for (SootMethod sm : new ArrayList<>(sc.getMethods())) {
                if (!sm.isConcrete()) {
                    continue;
                }
                Body body;
                try {
                    body = sm.retrieveActiveBody();
                } catch (Throwable ignored) {
                    // A body Soot cannot build tells us nothing; the rest of the
                    // class is still worth reading.
                    continue;
                }
                for (Unit u : body.getUnits()) {
                    if (!(u instanceof Stmt)) {
                        continue;
                    }
                    Stmt stmt = (Stmt) u;
                    if (!stmt.containsInvokeExpr()) {
                        continue;
                    }
                    InvokeExpr ie = stmt.getInvokeExpr();
                    String declaring = ie.getMethodRef().getDeclaringClass().getName();
                    String called = ie.getMethodRef().getName();
                    boolean intentLike = declaring.equals("android.content.Intent")
                            || declaring.equals("android.os.Bundle");
                    if (!intentLike) {
                        continue;
                    }
                    if (called.equals("getData") || called.equals("getDataString")) {
                        data = true;
                        continue;
                    }
                    String type = EXTRA_GETTERS.get(called);
                    if (type == null || ie.getArgCount() == 0) {
                        continue;
                    }
                    Value key = ie.getArg(0);
                    // Only a literal key can be reproduced on a command line. A
                    // computed key is reported by omission rather than guessed at.
                    if (key instanceof StringConstant) {
                        found.putIfAbsent(((StringConstant) key).value, type);
                    }
                }
            }
            if (!found.isEmpty() || data) {
                extras.put(name, found);
                readsData.put(name, data);
            }
        }
        System.out.println(toJson(extras, readsData));
    }

    /** Whether the class is an Activity, by walking its superclass chain. */
    private static boolean isActivity(SootClass sc) {
        SootClass cursor = sc;
        int guard = 0;
        while (cursor != null && guard++ < 40) {
            String n = cursor.getName();
            if (n.equals("android.app.Activity")) {
                return true;
            }
            if (!cursor.hasSuperclass()) {
                return false;
            }
            cursor = cursor.getSuperclass();
        }
        return false;
    }

    private static String toJson(Map<String, LinkedHashMap<String, String>> extras,
                                 Map<String, Boolean> readsData) {
        StringBuilder out = new StringBuilder("{\"activities\":{");
        boolean firstActivity = true;
        for (Map.Entry<String, LinkedHashMap<String, String>> e : extras.entrySet()) {
            if (!firstActivity) {
                out.append(',');
            }
            firstActivity = false;
            out.append(quote(e.getKey())).append(":{\"extras\":[");
            boolean firstExtra = true;
            for (Map.Entry<String, String> x : e.getValue().entrySet()) {
                if (!firstExtra) {
                    out.append(',');
                }
                firstExtra = false;
                out.append("{\"key\":").append(quote(x.getKey()))
                   .append(",\"type\":").append(quote(x.getValue())).append('}');
            }
            out.append("],\"reads_data_uri\":")
               .append(Boolean.TRUE.equals(readsData.get(e.getKey())));
            out.append('}');
        }
        return out.append("}}").toString();
    }

    private static String quote(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"':  b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        return b.append('"').toString();
    }
}
