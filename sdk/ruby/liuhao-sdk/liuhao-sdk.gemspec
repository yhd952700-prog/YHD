lib = File.expand_path("lib", __dir__)
$LOAD_PATH.unshift(lib) unless $LOAD_PATH.include?(lib)

Gem::Specification.new do |spec|
  spec.name          = "liuhao-sdk"
  spec.version       = "1.0.0"
  spec.authors       = ["LiuHao Team"]
  spec.email         = ["team@liuhao.ai"]

  spec.summary       = "Official Ruby SDK for the LiuHao AI OS platform"
  spec.description   = "A Ruby SDK for interacting with the LiuHao AI OS API. " \
                       "Supports agents, memory, knowledge bases, tasks, and health checks."
  spec.homepage      = "https://github.com/liuhao-ai/liuhao-os"
  spec.license       = "MIT"

  spec.required_ruby_version = ">= 2.7.0"

  spec.files         = Dir["lib/**/*", "README.md", "LICENSE"]
  spec.require_paths = ["lib"]

  spec.add_dependency "faraday", "~> 2.0"
  spec.add_dependency "faraday-net_http", "~> 3.0"
  spec.add_dependency "json", "~> 2.0"

  spec.add_development_dependency "bundler", "~> 2.0"
  spec.add_development_dependency "rake", "~> 13.0"
  spec.add_development_dependency "rspec", "~> 3.0"
  spec.add_development_dependency "webmock", "~> 3.0"
  spec.add_development_dependency "pry", "~> 0.14"
end
