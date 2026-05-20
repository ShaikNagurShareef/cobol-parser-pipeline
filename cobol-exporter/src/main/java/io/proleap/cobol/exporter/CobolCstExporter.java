package io.proleap.cobol.exporter;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

import org.antlr.v4.runtime.*;
import org.antlr.v4.runtime.tree.*;

import io.proleap.cobol.CobolLexer;
import io.proleap.cobol.CobolParser;
import io.proleap.cobol.CobolParser.*;
import io.proleap.cobol.asg.params.impl.CobolParserParamsImpl;
import io.proleap.cobol.preprocessor.CobolPreprocessor.CobolSourceFormatEnum;
import io.proleap.cobol.preprocessor.impl.CobolPreprocessorImpl;

/**
 * Parses a COBOL file using ProLeap and emits compact JSON capturing
 * paragraphs, data items, statements, CALL/EXEC targets, and COPY usage.
 *
 * Usage: java -jar cobol-exporter.jar <cobol-file> [copybook-dir...]
 */
public class CobolCstExporter {

    static final List<String> parseErrors = new ArrayList<>();
    static final List<String> preprocessErrors = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: CobolCstExporter <cobol-file> [copybook-dir...]");
            System.exit(1);
        }

        File cobolFile = new File(args[0]);
        if (!cobolFile.exists()) {
            System.err.println("File not found: " + cobolFile.getAbsolutePath());
            System.exit(2);
        }

        List<File> copyDirs = new ArrayList<>();
        for (int i = 1; i < args.length; i++) {
            File d = new File(args[i]);
            if (d.exists() && d.isDirectory()) copyDirs.add(d);
        }

        CobolParserParamsImpl params = new CobolParserParamsImpl();
        params.setCopyBookDirectories(copyDirs);
        params.setIgnoreSyntaxErrors(true);
        params.setFormat(CobolSourceFormatEnum.FIXED);

        String preprocessed;
        try {
            preprocessed = new CobolPreprocessorImpl().process(cobolFile, params);
        } catch (Exception e) {
            String msg = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
            preprocessErrors.add(msg.replace("\"", "'").replace("\n", " ").replace("\r", ""));
            try {
                preprocessed = new String(java.nio.file.Files.readAllBytes(cobolFile.toPath()), StandardCharsets.UTF_8);
            } catch (Exception e2) {
                preprocessed = "";
            }
        }

        CobolLexer lexer = new CobolLexer(CharStreams.fromString(preprocessed));
        lexer.removeErrorListeners();
        CommonTokenStream tokens = new CommonTokenStream(lexer);
        CobolParser parser = new CobolParser(tokens);
        parser.removeErrorListeners();
        parser.addErrorListener(new BaseErrorListener() {
            @Override
            public void syntaxError(Recognizer<?, ?> rec, Object sym,
                                    int line, int col, String msg, RecognitionException e) {
                parseErrors.add(line + ":" + col + " " + msg.replace("\"", "'").replace("\n", " "));
            }
        });

        StartRuleContext tree = parser.startRule();

        StringBuilder sb = new StringBuilder();
        sb.append("{");
        appendStr(sb, "file", cobolFile.getAbsolutePath());
        sb.append(",");
        sb.append("\"parse_errors\":").append(toJsonStringArray(parseErrors)).append(",");
        sb.append("\"preprocess_errors\":").append(toJsonStringArray(preprocessErrors)).append(",");
        sb.append("\"compilation_units\":[");

        List<CompilationUnitContext> cus = tree.getRuleContexts(CompilationUnitContext.class);
        for (int i = 0; i < cus.size(); i++) {
            if (i > 0) sb.append(",");
            serializeCompilationUnit(cus.get(i), sb, cobolFile.getName());
        }
        sb.append("]}");

        // Write to stdout
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        out.println(sb.toString());
    }

    // ─── Compilation Unit ────────────────────────────────────────────────────

    static void serializeCompilationUnit(CompilationUnitContext cu, StringBuilder sb, String fileName) {
        sb.append("{");
        String programName = extractProgramName(cu);
        if (programName == null) programName = stripExt(fileName);
        appendStr(sb, "name", programName);
        sb.append(",\"start_line\":").append(getStart(cu));
        sb.append(",\"end_line\":").append(getEnd(cu));
        sb.append(",\"data_items\":").append(serializeDataItems(cu));
        sb.append(",\"file_control\":").append(serializeFileControl(cu));
        sb.append(",\"paragraphs\":").append(serializeParagraphs(cu));
        sb.append(",\"copy_statements\":").append(serializeCopyStatements(cu));
        sb.append(",\"call_statements\":").append(serializeCallStatements(cu));
        sb.append(",\"exec_cics\":").append(serializeExecCics(cu));
        sb.append(",\"exec_sql\":").append(serializeExecSql(cu));
        sb.append("}");
    }

    // ─── Program Name ────────────────────────────────────────────────────────

    static String extractProgramName(CompilationUnitContext cu) {
        List<ProgramIdParagraphContext> pidList =
                findAll(cu, ProgramIdParagraphContext.class);
        if (!pidList.isEmpty()) {
            ProgramIdParagraphContext pid = pidList.get(0);
            ProgramNameContext pn = findFirst(pid, ProgramNameContext.class);
            if (pn != null) return pn.getText();
        }
        return null;
    }

    // ─── Data Items ─────────────────────────────────────────────────────────

    static String serializeDataItems(CompilationUnitContext cu) {
        List<DataDescriptionEntryContext> entries =
                findAll(cu, DataDescriptionEntryContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (DataDescriptionEntryContext e : entries) {
            if (!first) sb.append(",");
            first = false;
            serializeDataItem(e, sb);
        }
        sb.append("]");
        return sb.toString();
    }

    static void serializeDataItem(DataDescriptionEntryContext e, StringBuilder sb) {
        sb.append("{");

        // Level number
        DataDescriptionEntryFormat1Context f1 = findFirst(e, DataDescriptionEntryFormat1Context.class);
        DataDescriptionEntryFormat2Context f2 = findFirst(e, DataDescriptionEntryFormat2Context.class);
        DataDescriptionEntryFormat3Context f3 = findFirst(e, DataDescriptionEntryFormat3Context.class);

        int level = -1;
        String name = null;
        String pic = null;
        String usage = null;
        String redefines = null;
        String occurs = null;
        String value = null;
        String sign = null;
        boolean isOccursDependingOn = false;

        if (f1 != null) {
            // Level number is the first child token (INTEGERLITERAL or LEVEL_NUMBER_77)
            if (f1.getChildCount() > 0) {
                try { level = Integer.parseInt(f1.getChild(0).getText()); }
                catch (NumberFormatException ex) { level = 77; }
            }
            DataNameContext dn = findFirst(f1, DataNameContext.class);
            if (dn != null) name = dn.getText();

            // PIC
            DataPictureClauseContext pc = findFirst(f1, DataPictureClauseContext.class);
            if (pc != null) {
                PictureStringContext ps = findFirst(pc, PictureStringContext.class);
                if (ps != null) pic = ps.getText();
            }

            // USAGE
            DataUsageClauseContext uc = findFirst(f1, DataUsageClauseContext.class);
            if (uc != null) usage = extractUsage(uc);

            // REDEFINES
            DataRedefinesClauseContext rc = findFirst(f1, DataRedefinesClauseContext.class);
            if (rc != null) {
                DataNameContext rdn = findFirst(rc, DataNameContext.class);
                if (rdn != null) redefines = rdn.getText();
            }

            // OCCURS
            DataOccursClauseContext oc = findFirst(f1, DataOccursClauseContext.class);
            if (oc != null) {
                occurs = oc.getText();
                DataOccursDependingContext dep =
                        findFirst(oc, DataOccursDependingContext.class);
                isOccursDependingOn = (dep != null);
            }

            // SIGN
            DataSignClauseContext sc = findFirst(f1, DataSignClauseContext.class);
            if (sc != null) sign = sc.getText();

            // VALUE
            DataValueClauseContext vc = findFirst(f1, DataValueClauseContext.class);
            if (vc != null) value = vc.getText().length() > 80 ? vc.getText().substring(0, 80) : vc.getText();

        } else if (f2 != null) {
            // 66 level RENAMES
            level = 66;
            DataNameContext dn = findFirst(f2, DataNameContext.class);
            if (dn != null) name = dn.getText();
        } else if (f3 != null) {
            // 88 level condition
            level = 88;
            ConditionNameContext cn = findFirst(f3, ConditionNameContext.class);
            if (cn != null) name = cn.getText();
            DataValueClauseContext vc = findFirst(f3, DataValueClauseContext.class);
            if (vc != null) value = vc.getText().length() > 80 ? vc.getText().substring(0, 80) : vc.getText();
        }

        sb.append("\"level\":").append(level).append(",");
        appendStr(sb, "name", name != null ? name : "FILLER");
        sb.append(",\"start_line\":").append(getStart(e));
        sb.append(",\"end_line\":").append(getEnd(e));
        appendOptStr(sb, "pic", pic);
        appendOptStr(sb, "usage", usage);
        appendOptStr(sb, "redefines", redefines);
        appendOptStr(sb, "occurs", occurs);
        appendOptStr(sb, "sign", sign);
        appendOptStr(sb, "value", value);
        sb.append(",\"occurs_depending_on\":").append(isOccursDependingOn);
        sb.append("}");
    }

    static String extractUsage(DataUsageClauseContext uc) {
        String t = uc.getText().toUpperCase();
        if (t.contains("COMP-3") || t.contains("COMPUTATIONAL-3")) return "COMP-3";
        if (t.contains("COMP-4") || t.contains("COMPUTATIONAL-4")) return "COMP-4";
        if (t.contains("COMP-1") || t.contains("COMPUTATIONAL-1")) return "COMP-1";
        if (t.contains("COMP-2") || t.contains("COMPUTATIONAL-2")) return "COMP-2";
        if (t.contains("COMP") || t.contains("COMPUTATIONAL")) return "COMP";
        if (t.contains("BINARY")) return "BINARY";
        if (t.contains("DISPLAY")) return "DISPLAY";
        if (t.contains("PACKED-DECIMAL")) return "COMP-3";
        if (t.contains("INDEX")) return "INDEX";
        if (t.contains("POINTER")) return "POINTER";
        return uc.getText();
    }

    // ─── File Control ────────────────────────────────────────────────────────

    static String serializeFileControl(CompilationUnitContext cu) {
        List<FileControlEntryContext> entries = findAll(cu, FileControlEntryContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (FileControlEntryContext e : entries) {
            if (!first) sb.append(",");
            first = false;
            sb.append("{");
            SelectClauseContext sel = findFirst(e, SelectClauseContext.class);
            String fname = null;
            if (sel != null) {
                FileNameContext fn = findFirst(sel, FileNameContext.class);
                if (fn != null) fname = fn.getText();
            }
            appendStr(sb, "name", fname);
            sb.append(",\"start_line\":").append(getStart(e));
            AssignClauseContext ac = findFirst(e, AssignClauseContext.class);
            if (ac != null) {
                sb.append(",");
                appendStr(sb, "assign_to", ac.getText());
            }
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    // ─── Paragraphs ──────────────────────────────────────────────────────────

    static String serializeParagraphs(CompilationUnitContext cu) {
        List<ParagraphContext> paragraphs = findAll(cu, ParagraphContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (ParagraphContext p : paragraphs) {
            if (!first) sb.append(",");
            first = false;
            sb.append("{");
            ParagraphNameContext pn = findFirst(p, ParagraphNameContext.class);
            String paraName = pn != null ? pn.getText() : "UNNAMED";
            appendStr(sb, "name", paraName);
            sb.append(",\"start_line\":").append(getStart(p));
            sb.append(",\"end_line\":").append(getEnd(p));
            sb.append(",\"statements\":").append(serializeStatements(p));
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    static String serializeStatements(ParagraphContext p) {
        List<StatementContext> stmts = findAll(p, StatementContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (StatementContext s : stmts) {
            String json = serializeStatement(s);
            if (json != null) {
                if (!first) sb.append(",");
                first = false;
                sb.append(json);
            }
        }
        sb.append("]");
        return sb.toString();
    }

    static String serializeStatement(StatementContext s) {
        StringBuilder sb = new StringBuilder();

        if (findFirst(s, MoveStatementContext.class) != null) {
            sb.append("{\"kind\":\"MOVE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 60));
            sb.append("}");
        } else if (findFirst(s, PerformStatementContext.class) != null) {
            sb.append("{\"kind\":\"PERFORM\"");
            sb.append(",\"start_line\":").append(getStart(s));
            PerformStatementContext ps = findFirst(s, PerformStatementContext.class);
            ProcedureNameContext proc = findFirst(ps, ProcedureNameContext.class);
            if (proc != null) { sb.append(","); appendStr(sb, "target", proc.getText()); }
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, ComputeStatementContext.class) != null) {
            sb.append("{\"kind\":\"COMPUTE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 100));
            sb.append("}");
        } else if (findFirst(s, AddStatementContext.class) != null) {
            sb.append("{\"kind\":\"ADD\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, SubtractStatementContext.class) != null) {
            sb.append("{\"kind\":\"SUBTRACT\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, MultiplyStatementContext.class) != null) {
            sb.append("{\"kind\":\"MULTIPLY\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, DivideStatementContext.class) != null) {
            sb.append("{\"kind\":\"DIVIDE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, IfStatementContext.class) != null) {
            IfStatementContext ifCtx = findFirst(s, IfStatementContext.class);
            sb.append("{\"kind\":\"IF\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 200));
            // G2: extract PERFORM targets from then / else branches
            sb.append(",\"then_performs\":"); appendPerformList(sb, ifCtx.ifThen());
            sb.append(",\"else_performs\":"); appendPerformList(sb, ifCtx.ifElse());
            sb.append("}");
        } else if (findFirst(s, EvaluateStatementContext.class) != null) {
            sb.append("{\"kind\":\"EVALUATE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, CallStatementContext.class) != null) {
            sb.append("{\"kind\":\"CALL\"");
            sb.append(",\"start_line\":").append(getStart(s));
            CallStatementContext cs = findFirst(s, CallStatementContext.class);
            CallUsingPhraseContext uph = findFirst(cs, CallUsingPhraseContext.class);
            // Get callee
            List<ParseTree> children = getChildren(cs);
            String callee = null;
            for (int i = 0; i < children.size(); i++) {
                ParseTree c = children.get(i);
                if (c instanceof TerminalNode) {
                    String txt = c.getText().toUpperCase();
                    if (txt.equals("CALL") && i + 1 < children.size()) {
                        ParseTree next = children.get(i + 1);
                        callee = next.getText().replace("'", "").replace("\"", "");
                        break;
                    }
                }
            }
            if (callee != null) { sb.append(","); appendStr(sb, "callee", callee); }
            sb.append("}");
        } else if (findFirst(s, WriteStatementContext.class) != null) {
            sb.append("{\"kind\":\"WRITE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, ReadStatementContext.class) != null) {
            sb.append("{\"kind\":\"READ\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, RewriteStatementContext.class) != null) {
            sb.append("{\"kind\":\"REWRITE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, DeleteStatementContext.class) != null) {
            sb.append("{\"kind\":\"DELETE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 60));
            sb.append("}");
        } else if (findFirst(s, InitializeStatementContext.class) != null) {
            sb.append("{\"kind\":\"INITIALIZE\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, StopStatementContext.class) != null) {
            sb.append("{\"kind\":\"STOP\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append("}");
        } else if (findFirst(s, GobackStatementContext.class) != null) {
            sb.append("{\"kind\":\"GOBACK\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append("}");
        } else if (findFirst(s, GoToStatementContext.class) != null) {
            sb.append("{\"kind\":\"GOTO\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 80));
            sb.append("}");
        } else if (findFirst(s, ExecCicsStatementContext.class) != null) {
            ExecCicsStatementContext ec = findFirst(s, ExecCicsStatementContext.class);
            String verb = extractCicsVerb(ec);
            sb.append("{\"kind\":\"EXEC_CICS\"");
            sb.append(",\"start_line\":").append(getStart(s));
            appendOptStr(sb, "verb", verb);
            sb.append(",\"text\":"); appendJsonStr(sb, getText(ec, 120));
            sb.append("}");
        } else if (findFirst(s, ExecSqlStatementContext.class) != null) {
            ExecSqlStatementContext es = findFirst(s, ExecSqlStatementContext.class);
            sb.append("{\"kind\":\"EXEC_SQL\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(es, 120));
            sb.append("}");
        } else {
            // Generic fallback
            sb.append("{\"kind\":\"OTHER\"");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 60));
            sb.append("}");
        }

        return sb.length() > 0 ? sb.toString() : null;
    }

    // ─── COPY Statements ─────────────────────────────────────────────────────
    // Note: ProLeap preprocessor resolves COPY before parsing, so COPY
    // statements do not appear in the parse tree. We return an empty list here;
    // the Python layer extracts copybook usage from the raw source separately.

    static String serializeCopyStatements(CompilationUnitContext cu) {
        return "[]";
    }

    // ─── CALL Statements ─────────────────────────────────────────────────────

    static String serializeCallStatements(CompilationUnitContext cu) {
        List<CallStatementContext> calls = findAll(cu, CallStatementContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (CallStatementContext c : calls) {
            if (!first) sb.append(",");
            first = false;
            sb.append("{");

            // Extract callee — first literal or identifier after CALL
            List<ParseTree> children = getChildren(c);
            String callee = null;
            boolean isLiteral = false;
            for (int i = 0; i < children.size(); i++) {
                ParseTree ch = children.get(i);
                if (ch instanceof TerminalNode && ch.getText().equalsIgnoreCase("CALL")
                        && i + 1 < children.size()) {
                    ParseTree next = children.get(i + 1);
                    String txt = next.getText();
                    isLiteral = txt.startsWith("'") || txt.startsWith("\"");
                    callee = txt.replace("'", "").replace("\"", "");
                    break;
                }
            }
            appendStr(sb, "callee", callee != null ? callee : "");
            sb.append(",\"literal\":").append(isLiteral);
            sb.append(",\"start_line\":").append(getStart(c));
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    // ─── EXEC CICS ────────────────────────────────────────────────────────────

    static String serializeExecCics(CompilationUnitContext cu) {
        List<ExecCicsStatementContext> stmts = findAll(cu, ExecCicsStatementContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (ExecCicsStatementContext s : stmts) {
            if (!first) sb.append(",");
            first = false;
            sb.append("{");
            String verb = extractCicsVerb(s);
            appendStr(sb, "verb", verb != null ? verb : "");
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 150));
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    static String extractCicsVerb(ExecCicsStatementContext s) {
        String[] verbs = {"LINK", "XCTL", "RETURN", "SEND", "RECEIVE", "READ",
                "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT", "GETMAIN",
                "FREEMAIN", "HANDLE", "ASKTIME", "FORMATTIME", "ENDBR", "RESETBR",
                "ABEND", "IGNORE", "SYNCPOINT", "ADDRESS", "ASSIGN", "GET", "PUT",
                "QUERY", "DEFINE", "CANCEL", "DELAY"};
        String text = s.getText().toUpperCase();
        for (String v : verbs) {
            if (text.contains(v)) return v;
        }
        return null;
    }

    // ─── EXEC SQL ─────────────────────────────────────────────────────────────

    static String serializeExecSql(CompilationUnitContext cu) {
        List<ExecSqlStatementContext> stmts = findAll(cu, ExecSqlStatementContext.class);
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (ExecSqlStatementContext s : stmts) {
            if (!first) sb.append(",");
            first = false;
            sb.append("{");
            String text = getText(s, 200).toUpperCase();
            String op = "UNKNOWN";
            for (String o : new String[]{"SELECT", "INSERT", "UPDATE", "DELETE", "DECLARE", "OPEN", "FETCH", "CLOSE", "CALL"}) {
                if (text.contains(o)) { op = o; break; }
            }
            appendStr(sb, "operation", op);
            sb.append(",\"start_line\":").append(getStart(s));
            sb.append(",\"text\":"); appendJsonStr(sb, getText(s, 200));
            sb.append("}");
        }
        sb.append("]");
        return sb.toString();
    }

    // ─── Utilities ───────────────────────────────────────────────────────────

    @SuppressWarnings("unchecked")
    static <T extends ParseTree> List<T> findAll(ParseTree root, Class<T> clazz) {
        List<T> result = new ArrayList<>();
        findAllHelper(root, clazz, result);
        return result;
    }

    @SuppressWarnings("unchecked")
    static <T extends ParseTree> void findAllHelper(ParseTree node, Class<T> clazz, List<T> result) {
        if (clazz.isInstance(node)) result.add((T) node);
        for (int i = 0; i < node.getChildCount(); i++) {
            findAllHelper(node.getChild(i), clazz, result);
        }
    }

    @SuppressWarnings("unchecked")
    static <T extends ParseTree> T findFirst(ParseTree root, Class<T> clazz) {
        if (clazz.isInstance(root)) return (T) root;
        for (int i = 0; i < root.getChildCount(); i++) {
            T result = findFirst(root.getChild(i), clazz);
            if (result != null) return result;
        }
        return null;
    }

    static List<ParseTree> getChildren(ParseTree node) {
        List<ParseTree> children = new ArrayList<>();
        for (int i = 0; i < node.getChildCount(); i++) {
            children.add(node.getChild(i));
        }
        return children;
    }

    static int getStart(ParseTree node) {
        if (node instanceof ParserRuleContext) {
            Token t = ((ParserRuleContext) node).start;
            return t != null ? t.getLine() : -1;
        }
        return -1;
    }

    static int getEnd(ParseTree node) {
        if (node instanceof ParserRuleContext) {
            Token t = ((ParserRuleContext) node).stop;
            return t != null ? t.getLine() : -1;
        }
        return -1;
    }

    static String getText(ParseTree node, int maxLen) {
        String t = node.getText();
        if (t.length() > maxLen) t = t.substring(0, maxLen) + "...";
        return t;
    }

    static String stripExt(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot > 0 ? filename.substring(0, dot) : filename;
    }

    static String toJsonStringArray(List<String> items) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < items.size(); i++) {
            if (i > 0) sb.append(",");
            appendJsonStr(sb, items.get(i));
        }
        sb.append("]");
        return sb.toString();
    }

    static void appendStr(StringBuilder sb, String key, String value) {
        sb.append("\"").append(key).append("\":");
        appendJsonStr(sb, value != null ? value : "");
    }

    static void appendOptStr(StringBuilder sb, String key, String value) {
        sb.append(",\"").append(key).append("\":");
        if (value == null) sb.append("null");
        else appendJsonStr(sb, value);
    }

    /** G2: emit JSON array of PERFORM paragraph targets found in an if-branch.
     *  Uses findFirst to traverse the full subtree of each statement. */
    static void appendPerformList(StringBuilder sb, ParserRuleContext branch) {
        sb.append("[");
        if (branch == null) { sb.append("]"); return; }
        boolean first = true;
        // branch.statement() returns direct StatementContext children
        List<StatementContext> stmts = branch.getRuleContexts(StatementContext.class);
        for (StatementContext stmt : stmts) {
            // findFirst does a deep DFS — traverses PerformStatement → PerformProcedureStatement
            PerformProcedureStatementContext pp = findFirst(stmt, PerformProcedureStatementContext.class);
            if (pp == null) continue;
            List<ProcedureNameContext> names = pp.procedureName();
            if (names == null || names.isEmpty()) continue;
            String name = names.get(0).getText().toUpperCase().trim();
            if (!name.isEmpty()) {
                if (!first) sb.append(",");
                appendJsonStr(sb, name);
                first = false;
            }
        }
        sb.append("]");
    }

    static void appendJsonStr(StringBuilder sb, String value) {
        if (value == null) { sb.append("null"); return; }
        sb.append("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        sb.append("\"");
    }

    static String escapeJson(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
