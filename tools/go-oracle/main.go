// go-oracle validates UWS documents with the upstream Go reference validator and prints one JSON
// line per input: {"file": ..., "valid": bool, "error": "..."}. Used by uws-py gate G6.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"os"
	"reflect"
	"runtime/debug"
	"strings"

	"github.com/OpenUdon/uws/convert"
	"github.com/OpenUdon/uws/expressions"
	"github.com/OpenUdon/uws/mockruntime"
	"github.com/OpenUdon/uws/schemas"
	"github.com/OpenUdon/uws/uws1"
	"github.com/OpenUdon/uws/validation"
	"github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

type verdict struct {
	File  string `json:"file"`
	Kind  string `json:"kind"`
	Valid bool   `json:"valid"`
	Error string `json:"error,omitempty"`
	// Value is the decoded JSON for the decode-only kinds.
	Value json.RawMessage `json:"value,omitempty"`
}

func check(kind, path string) verdict {
	v := verdict{File: path, Kind: kind}
	var err error
	switch kind {
	case "document":
		_, err = validation.ValidateDocumentFile(path)
	case "executable":
		doc, verr := validation.ValidateDocumentFile(path)
		err = verr
		if err == nil {
			err = doc.ValidateExecutable()
		}
	case "browser-profile":
		err = withData(path, schemas.ValidateBrowserSourceProfile)
	case "browser-authentication":
		err = withData(path, schemas.ValidateBrowserAuthenticationProfile)
	case "mock-fixtures":
		err = withData(path, func(data []byte) error { _, err := mockruntime.DecodeFixtures(data); return err })
	case "browser-authentication-call-1.0":
		err = withData(path, func(data []byte) error {
			return schemas.ValidateBrowserAuthenticationCallSupplementForProfile(data, "uws.browser-authentication-call.1.0")
		})
	case "browser-authentication-call-1.1":
		err = withData(path, func(data []byte) error {
			return schemas.ValidateBrowserAuthenticationCallSupplementForProfile(data, "uws.browser-authentication-call.1.1")
		})
	case "yaml-to-json":
		// The YAML half of uws/convert.UnmarshalYAML: the JSON that is then decoded into uws1.Document.
		err = withData(path, func(data []byte) error {
			out, err := convert.YAMLToJSON(data)
			v.Value = out
			return err
		})
	case "json-unmarshal":
		// encoding/json into an interface value, as for untyped document members.
		err = withData(path, func(data []byte) error {
			var value any
			if err := json.Unmarshal(data, &value); err != nil {
				return err
			}
			out, err := json.Marshal(value)
			v.Value = out
			return err
		})
	case "schema-check":
		// {"schema": {...}, "value": ...}: santhosh-tekuri/jsonschema with format assertion, as upstream
		// compiles the published schemas.
		err = withData(path, schemaCheck)
	case "url-parse":
		// The file content is a URL; the value is Hostname/Port and loopback as Go computes them.
		err = withData(path, func(data []byte) error {
			u, err := url.Parse(string(data))
			if err != nil {
				return err
			}
			out, err := json.Marshal(map[string]any{
				"scheme": u.Scheme, "opaque": u.Opaque, "host": u.Host, "path": u.Path, "rawQuery": u.RawQuery,
				"fragment": u.Fragment, "hostname": u.Hostname(), "port": u.Port(), "hasUser": u.User != nil,
				"loopback": net.ParseIP(u.Hostname()).IsLoopback(),
			})
			v.Value = out
			return err
		})
	case "yaml-any":
		// yaml.v3 into an interface value, as the raw-document schema check reads YAML.
		err = withData(path, func(data []byte) error {
			var value any
			return yaml.Unmarshal(data, &value)
		})
	default:
		err = fmt.Errorf("unknown kind %q", kind)
	}
	v.Valid = err == nil
	if err != nil {
		v.Error = err.Error()
		v.Value = nil
	}
	return v
}

func withData(path string, fn func([]byte) error) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return fn(data)
}

type expressionCase struct {
	Version string `json:"version"`
	Field   string `json:"field"`
	InLoop  bool   `json:"inLoop"`
	Text    string `json:"text"`
}

// checkExpressions reads a JSON array of expression cases and emits one verdict per case using the
// upstream reference parser (expressions.Parse).
func checkExpressions(path string, out *json.Encoder) {
	data, err := os.ReadFile(path)
	if err != nil {
		_ = out.Encode(verdict{File: path, Kind: "expressions", Error: err.Error()})
		return
	}
	var cases []expressionCase
	if err := json.Unmarshal(data, &cases); err != nil {
		_ = out.Encode(verdict{File: path, Kind: "expressions", Error: err.Error()})
		return
	}
	fields := map[string]expressions.Field{"value": expressions.Value, "predicate": expressions.Predicate, "wait": expressions.Wait, "batchSize": expressions.BatchSize}
	for i, c := range cases {
		v := verdict{File: fmt.Sprintf("%s#%d", path, i), Kind: "expression"}
		field, ok := fields[c.Field]
		if !ok {
			field = expressions.Field(250)
		}
		_, err := expressions.Parse(c.Text, expressions.Context{Version: c.Version, Field: field, InLoop: c.InLoop})
		v.Valid = err == nil
		if err != nil {
			v.Error = err.Error()
		}
		_ = out.Encode(v)
	}
}

func schemaCheck(data []byte) error {
	wrapper, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err != nil {
		return err
	}
	obj, ok := wrapper.(map[string]any)
	if !ok {
		return fmt.Errorf("schema-check input must be an object")
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	if err := compiler.AddResource("inline.json", obj["schema"]); err != nil {
		return err
	}
	schema, err := compiler.Compile("inline.json")
	if err != nil {
		return err
	}
	return schema.Validate(obj["value"])
}

// structModel describes the Go types encoding/json decodes a uws1.Document into, for uws-py's struct-decoding
// model (src/openudon_uws/_gomodel.json).
func structModel() map[string]any {
	types := map[string]any{}
	unmarshaler := reflect.TypeOf((*json.Unmarshaler)(nil)).Elem()
	var describe func(t reflect.Type) any
	describe = func(t reflect.Type) any {
		switch t.Kind() {
		case reflect.Pointer:
			return map[string]any{"kind": "ptr", "elem": describe(t.Elem())}
		case reflect.Slice:
			if t.Elem().Kind() == reflect.Uint8 {
				return map[string]any{"kind": "raw"}
			}
			return map[string]any{"kind": "slice", "elem": describe(t.Elem())}
		case reflect.Map:
			return map[string]any{"kind": "map", "elem": describe(t.Elem())}
		case reflect.Interface:
			return map[string]any{"kind": "any"}
		case reflect.String:
			return map[string]any{"kind": "string"}
		case reflect.Bool:
			return map[string]any{"kind": "bool"}
		case reflect.Int, reflect.Int64, reflect.Int32:
			return map[string]any{"kind": "int", "bits": t.Bits()}
		case reflect.Float64, reflect.Float32:
			return map[string]any{"kind": "float", "bits": t.Bits()}
		case reflect.Struct:
			name := t.Name()
			if _, done := types[name]; !done {
				types[name] = nil
				fields := []any{}
				var collect func(st reflect.Type)
				collect = func(st reflect.Type) {
					for i := 0; i < st.NumField(); i++ {
						f := st.Field(i)
						tag := f.Tag.Get("json")
						if f.Anonymous && tag == "" {
							collect(f.Type)
							continue
						}
						if !f.IsExported() || tag == "-" {
							continue
						}
						jsonName, _, _ := strings.Cut(tag, ",")
						if jsonName == "" {
							jsonName = f.Name
						}
						fields = append(fields, map[string]any{"name": jsonName, "type": describe(f.Type)})
					}
				}
				collect(t)
				types[name] = map[string]any{
					"fields":        fields,
					"unmarshalJSON": reflect.PointerTo(t).Implements(unmarshaler),
				}
			}
			return map[string]any{"kind": "struct", "name": name}
		default:
			return map[string]any{"kind": "unsupported:" + t.Kind().String()}
		}
	}
	root := describe(reflect.TypeOf(uws1.Document{}))
	return map[string]any{"root": root, "types": types}
}

// Input lines on stdin: "<kind>\t<path>".
func main() {
	// Inputs that make the reference implementation recurse without bound (for example a YAML anchor that
	// contains itself) must fail fast instead of exhausting memory; the harness reports them as crashes.
	debug.SetMaxStack(64 << 20)
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1<<20), 1<<20)
	out := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		line := scanner.Text()
		var kind, path string
		for i := 0; i < len(line); i++ {
			if line[i] == '\t' {
				kind, path = line[:i], line[i+1:]
				break
			}
		}
		if kind == "" {
			continue
		}
		if kind == "struct-model" {
			model, _ := json.Marshal(structModel())
			_ = out.Encode(verdict{File: path, Kind: kind, Valid: true, Value: model})
			continue
		}
		if kind == "expressions" {
			checkExpressions(path, out)
			continue
		}
		_ = out.Encode(check(kind, path))
	}
}
