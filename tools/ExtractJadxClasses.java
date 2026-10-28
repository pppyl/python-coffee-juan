// Offline, selected-class extraction using the existing JADX public Java API.
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.regex.Pattern;
import jadx.api.*;

public class ExtractJadxClasses {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("input-dex-or-directory output-dir class-regex");
        Path input = Path.of(args[0]), output = Path.of(args[1]);
        Pattern filter = Pattern.compile(args[2]);
        Files.createDirectories(output);
        List<File> files = new ArrayList<>();
        if (Files.isDirectory(input)) {
            try (var stream = Files.list(input)) {
                stream.filter(p -> p.toString().endsWith(".dex")).sorted().forEach(p -> files.add(p.toFile()));
            }
        } else files.add(input.toFile());
        JadxArgs config = new JadxArgs();
        config.setInputFiles(files);
        config.setThreadsCount(1);
        config.setSkipResources(true);
        config.setDeobfuscationOn(false);
        config.setShowInconsistentCode(true);
        config.setInlineMethods(false);
        config.setClassFilter(name -> filter.matcher(name).find());
        try (JadxDecompiler decompiler = new JadxDecompiler(config)) {
            decompiler.load();
            List<String> inventory = new ArrayList<>();
            for (JavaClass cls : decompiler.getClassesWithInners()) {
                String name = cls.getRawName();
                inventory.add(name);
                if (!filter.matcher(name).find()) continue;
                Path destination = output.resolve(name.replace('.', '/') + ".java");
                Files.createDirectories(destination.getParent());
                try {
                    Files.writeString(destination, cls.getCode(), StandardCharsets.UTF_8);
                    System.out.println("WROTE " + name);
                } catch (Exception ex) {
                    System.out.println("FAILED " + name + " " + ex.getClass().getSimpleName());
                }
            }
            Files.write(output.resolve("class-inventory.txt"), inventory, StandardCharsets.UTF_8);
            System.out.println("CLASSES " + inventory.size());
        }
    }
}
